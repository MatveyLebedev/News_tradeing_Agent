"""
Fundamentals fetcher — writes to the `fundamentals` table for the long-term agent.

Two sources, same table (see create_fundamentals_table.sql):

  EDGAR (historical, true point-in-time):
      As-filed income / balance / cash-flow statements with the original filing
      date. Backfilled once, cheaply topped up afterwards. This is the data the
      backtest reads with `WHERE filed_date <= as_of`.

  yfinance (current snapshots, forward PIT archive):
      Valuation ratios / margins (`info`), analyst estimates + revisions, and
      earnings dates. None of these live in SEC filings. Each run stores a
      snapshot keyed by `fetch_date`; over time this becomes a point-in-time
      archive the agent reads with `WHERE fetch_date <= as_of`.

Pattern matches price_fetcher.py / news_fetcher.py: fetchers write, the agent reads.
"""
import os
import math
from datetime import datetime, date

import pytz
import requests
import yfinance as yf
import psycopg2
from psycopg2.extras import Json, execute_values

from config import DB_CONFIG, FMP_API_KEY
from logger import DatabaseLogger
from edgar_fetcher import fetch_fundamentals_edgar, load_cik_map, _session as edgar_session
from price_fetcher import TOP_50_TICKERS

logger = DatabaseLogger()
EASTERN_TZ = pytz.timezone("America/New_York")

# Curated valuation/profitability keys we pull from yfinance `info`.
INFO_KEYS = [
    "trailingPE", "forwardPE", "priceToSalesTrailing12Months", "priceToBook",
    "enterpriseToEbitda", "enterpriseToRevenue", "marketCap", "enterpriseValue",
    "profitMargins", "grossMargins", "operatingMargins", "ebitdaMargins",
    "returnOnEquity", "returnOnAssets", "revenueGrowth", "earningsGrowth",
    "totalDebt", "totalCash", "freeCashflow", "operatingCashflow",
    "sharesOutstanding", "sector", "industry", "beta",
    "targetMeanPrice", "targetMedianPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationKey", "recommendationMean",
    "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    # 9.5 short interest (FINRA bi-monthly figures as carried by yfinance; the weekly
    # snapshot cadence over-samples them, which is fine — dateShortInterest disambiguates)
    "sharesShort", "sharesShortPriorMonth", "shortRatio", "shortPercentOfFloat",
    "dateShortInterest",
]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_db_connection():
    return psycopg2.connect(
        host=DB_CONFIG["host"], database=DB_CONFIG["database"],
        user=DB_CONFIG["user"], password=DB_CONFIG["password"], port=DB_CONFIG["port"],
    )


def ensure_table():
    """Create the fundamentals table from the SQL file if it doesn't exist."""
    sql_path = os.path.join(os.path.dirname(__file__), "create_fundamentals_table.sql")
    try:
        with open(sql_path) as f:
            ddl = f.read()
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠ Could not ensure fundamentals table: {e}")
        logger.error("FUND_TABLE_ERROR", "Failed to ensure fundamentals table", details={"error": str(e)})
        return False


def _to_jsonable(obj):
    """Recursively convert pandas/numpy/datetime values into JSON-safe Python types."""
    if obj is None:
        return None
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    # numpy scalars / pandas Timestamp / Decimal etc.
    for attr in ("item", "isoformat"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return _to_jsonable(fn())
            except Exception:
                pass
    try:
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return str(obj)


def _df_to_records(df):
    """Convert a yfinance DataFrame to a list of {index, **row} JSON-safe dicts."""
    if df is None or getattr(df, "empty", True):
        return []
    out = []
    for idx, row in df.iterrows():
        rec = {"period": _to_jsonable(idx)}
        for col in df.columns:
            rec[str(col)] = _to_jsonable(row[col])
        out.append(rec)
    return out


def _dedup_key(ticker, source, kind, period_end, fiscal_period, filed_date, fetch_date):
    return "|".join(str(x) for x in [ticker, source, kind, period_end, fiscal_period, filed_date, fetch_date])


def save_records(records):
    """Bulk upsert fundamentals rows. Each record is a full row dict."""
    if not records:
        return 0
    rows = [(
        r["ticker"], r["source"], r["kind"], r.get("period_end"), r.get("fiscal_period"),
        r.get("filed_date"), r["fetch_date"], r["dedup_key"], Json(r["data"]),
    ) for r in records]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO fundamentals
                    (ticker, source, kind, period_end, fiscal_period, filed_date, fetch_date, dedup_key, data)
                VALUES %s
                ON CONFLICT (dedup_key) DO UPDATE SET data = EXCLUDED.data
            """, rows)
            n = cur.rowcount
        conn.commit()
        return n if n and n > 0 else len(rows)
    except Exception as e:
        conn.rollback()
        print(f"  ✗ DB insert failed: {e}")
        logger.error("FUND_DB_ERROR", "Fundamentals insert failed", details={"error": str(e)})
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# yfinance snapshot side
# ---------------------------------------------------------------------------
def build_yfinance_records(ticker, fetch_date):
    """Snapshot info / earnings_dates / analyst estimates / eps_revisions for one ticker."""
    recs = []
    try:
        t = yf.Ticker(ticker)
    except Exception as e:
        print(f"  ✗ {ticker}: yfinance init failed: {e}")
        return recs

    def add(kind, data):
        if not data:
            return
        recs.append({
            "ticker": ticker, "source": "yfinance", "kind": kind,
            "period_end": None, "fiscal_period": None, "filed_date": None,
            "fetch_date": fetch_date,
            "dedup_key": _dedup_key(ticker, "yfinance", kind, None, None, None, fetch_date),
            "data": data,
        })

    # 1. info snapshot (valuation, margins, targets)
    try:
        info = t.info or {}
        snap = {k: _to_jsonable(info.get(k)) for k in INFO_KEYS if info.get(k) is not None}
        add("info", snap)
    except Exception as e:
        print(f"  ⚠ {ticker}: info failed: {e}")

    # 2. earnings dates (history + upcoming, actual vs estimate)
    try:
        add("earnings_dates", {"records": _df_to_records(t.earnings_dates)})
    except Exception as e:
        print(f"  ⚠ {ticker}: earnings_dates failed: {e}")

    # 3. analyst estimates (forward EPS/revenue) + trend + realized surprises
    try:
        est = {
            "earnings_estimate": _df_to_records(t.earnings_estimate),
            "revenue_estimate": _df_to_records(t.revenue_estimate),
            "eps_trend": _df_to_records(t.eps_trend),
            "price_targets": _to_jsonable(getattr(t, "analyst_price_targets", None)),
            # consensus-side additions (guidance-vs-consensus groundwork, 2026-07-15):
            "growth_estimates": _df_to_records(getattr(t, "growth_estimates", None)),
            "earnings_history": _df_to_records(getattr(t, "earnings_history", None)),
        }
        if any(est.values()):
            add("analyst_estimates", est)
    except Exception as e:
        print(f"  ⚠ {ticker}: analyst_estimates failed: {e}")

    # 4. estimate revisions (the expectation-gap signal)
    try:
        add("eps_revisions", {"records": _df_to_records(t.eps_revisions)})
    except Exception as e:
        print(f"  ⚠ {ticker}: eps_revisions failed: {e}")

    return recs


# ---------------------------------------------------------------------------
# EDGAR side
# ---------------------------------------------------------------------------
def build_edgar_records(ticker, fetch_date, cik_map, session):
    recs = []
    for r in fetch_fundamentals_edgar(ticker, cik_map=cik_map, session=session):
        period_end = r["period_end"].isoformat()
        filed_date = r["filed_date"].isoformat()
        recs.append({
            "ticker": ticker, "source": "edgar", "kind": r["kind"],
            "period_end": period_end, "fiscal_period": r["fiscal_period"],
            "filed_date": filed_date, "fetch_date": fetch_date,
            "dedup_key": _dedup_key(ticker, "edgar", r["kind"], period_end,
                                    r["fiscal_period"], filed_date, None),
            "data": _to_jsonable(r["data"]),
        })
    return recs


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
# FMP side (free tier) — per-ticker earnings history + forward consensus
# ---------------------------------------------------------------------------
def build_fmp_records(ticker, fetch_date, api_key):
    """Snapshot FMP /stable/earnings for one ticker: past reports with epsActual/
    epsEstimated AND revenueActual/revenueEstimated (the revenue-surprise history
    yfinance lacks) + upcoming report dates with street consensus.

    PIT note: forward snapshots (this weekly job) are first-release by construction;
    the HISTORY inside a snapshot has no first-release guarantee from FMP (estimates
    may be frozen at report time, but that's their claim, not ours) — backtest features
    built on the historical part stay ADVISORY until forward data covers the window."""
    if not api_key:
        return []
    url = f"https://financialmodelingprep.com/stable/earnings?symbol={ticker}&apikey={api_key}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ⚠ {ticker}: FMP earnings failed: {type(e).__name__}: {str(e).replace(api_key, '***')}")
        return []
    if not isinstance(data, list) or not data:
        return []
    return [{
        "ticker": ticker, "source": "fmp", "kind": "earnings",
        "period_end": None, "fiscal_period": None, "filed_date": None,
        "fetch_date": fetch_date,
        "dedup_key": _dedup_key(ticker, "fmp", "earnings", None, None, None, fetch_date),
        "data": {"records": data},
    }]


def fetch_fundamentals(tickers=None, include_edgar=True, include_yf=True, verbose=True):
    """Fetch fundamentals for all tickers and persist them."""
    if tickers is None:
        tickers = TOP_50_TICKERS

    ensure_table()
    fetch_date = datetime.now(EASTERN_TZ).date().isoformat()

    cik_map, session = None, None
    if include_edgar:
        session = edgar_session()
        cik_map = load_cik_map(session)
    fmp_key = FMP_API_KEY
    if not fmp_key and verbose:
        print("  (FMP_API_KEY not set — skipping FMP earnings snapshots)")

    results = {"tickers": len(tickers), "edgar_rows": 0, "yf_rows": 0, "fmp_rows": 0, "failed": []}
    logger.info("FUND_FETCH_START", f"Fundamentals fetch for {len(tickers)} tickers",
                details={"tickers": len(tickers), "edgar": include_edgar, "yfinance": include_yf,
                         "fmp": bool(fmp_key)})

    for idx, ticker in enumerate(tickers, 1):
        if verbose:
            print(f"[{idx}/{len(tickers)}] {ticker} ...", flush=True)
        try:
            if include_edgar:
                e = build_edgar_records(ticker, fetch_date, cik_map, session)
                results["edgar_rows"] += save_records(e)
            if include_yf:
                y = build_yfinance_records(ticker, fetch_date)
                results["yf_rows"] += save_records(y)
            if fmp_key:
                f = build_fmp_records(ticker, fetch_date, fmp_key)
                results["fmp_rows"] += save_records(f)
        except Exception as ex:
            print(f"  ✗ {ticker}: {ex}")
            results["failed"].append(ticker)
            logger.error("FUND_FETCH_ERROR", f"Failed for {ticker}", details={"ticker": ticker, "error": str(ex)})

    if verbose:
        print(f"\n{'=' * 70}")
        print(f"FUNDAMENTALS FETCH COMPLETE  ({fetch_date})")
        print(f"  EDGAR statement rows:  {results['edgar_rows']}")
        print(f"  yfinance snapshot rows: {results['yf_rows']}")
        print(f"  FMP earnings rows:      {results['fmp_rows']}")
        print(f"  Failed: {len(results['failed'])} {results['failed'] or ''}")
        print(f"{'=' * 70}\n")

    logger.info("FUND_FETCH_COMPLETE",
                f"Fundamentals fetch complete: {results['edgar_rows']} edgar, {results['yf_rows']} yf, "
                f"{results['fmp_rows']} fmp",
                details=results)
    return results


def show_stats():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM fundamentals")
        total = cur.fetchone()[0]
        cur.execute("SELECT source, kind, count(*) FROM fundamentals GROUP BY 1,2 ORDER BY 1,2")
        rows = cur.fetchall()
        cur.execute("SELECT count(DISTINCT ticker) FROM fundamentals")
        tickers = cur.fetchone()[0]
        cur.execute("SELECT min(period_end), max(period_end) FROM fundamentals WHERE source='edgar'")
        pmin, pmax = cur.fetchone()
    conn.close()
    print(f"\nfundamentals: {total} rows across {tickers} tickers")
    print(f"EDGAR period range: {pmin} → {pmax}")
    for src, kind, n in rows:
        print(f"  {src:9s} {kind:18s} {n}")
    print()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Fetch company fundamentals (EDGAR PIT + yfinance snapshots)")
    ap.add_argument("--tickers", nargs="+", help="Specific tickers (default: top universe)")
    ap.add_argument("--edgar-only", action="store_true", help="Only fetch EDGAR statements")
    ap.add_argument("--yf-only", action="store_true", help="Only fetch yfinance snapshots")
    ap.add_argument("--stats", action="store_true", help="Show table stats and exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.stats:
        show_stats()
    else:
        fetch_fundamentals(
            tickers=args.tickers,
            include_edgar=not args.yf_only,
            include_yf=not args.edgar_only,
            verbose=not args.quiet,
        )
