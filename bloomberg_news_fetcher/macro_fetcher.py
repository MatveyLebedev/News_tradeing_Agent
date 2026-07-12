"""
Macro / regime series fetcher — fills `macro_series` (Modification Plan Phase 14).

Free, PIT-clean market-regime series (expert checklist #25-28, #31-33, #37): the point is
to STAMP every signal with the regime it was born in, so that once the data spans more
than one regime the strategy's regime-dependence becomes measurable. NO decision logic
reads these values today (collect NOW, gate LATER — gating on a single regime would be
curve-fitting).

Sources (no API keys):
  FRED  fredgraph.csv    hy_oas (BAMLH0A0HYM2, %), dgs10 (DGS10, %),
                         fed_bs (WALCL, $M), tga (WTREGEN, $B), rrp (RRPONTSYD, $B)
  yfinance               vix (^VIX), vix3m (^VIX3M), brent (BZ=F), wti (CL=F)

Known cap: FRED serves ICE BofA series (hy_oas) only for the trailing ~3 years
(licensing), regardless of cosd — so its "5y" percentile runs on whatever history exists
(≥60-obs guard in regime_tool) and grows toward a true 5y window as PIT rows accumulate.

PIT discipline: INSERT .. ON CONFLICT DO NOTHING — the FIRST value fetched for a
(series, date) is never overwritten by later revisions (unlike prices, which upsert).
Incremental runs re-scan a trailing window so FRED's 1-2 day publication lag and weekly
series (WALCL) heal without ever rewriting history.

CLI:
    python macro_fetcher.py                # incremental top-up (trailing window)
    python macro_fetcher.py --backfill     # full history (FRED 2010+, yfinance max)
    python macro_fetcher.py --stats
"""
import argparse
import csv
import io
import time
from datetime import date, datetime, timedelta

import requests
import yfinance as yf
from psycopg2.extras import execute_values

from daily_price_fetcher import get_db_connection
from logger import DatabaseLogger

FRED_SERIES = {
    "hy_oas": "BAMLH0A0HYM2",   # BofA US High Yield OAS (percent)
    "dgs10": "DGS10",           # 10Y Treasury constant maturity (percent)
    "fed_bs": "WALCL",          # Fed balance sheet total ($ millions, weekly)
    "tga": "WTREGEN",           # Treasury General Account ($ billions, weekly)
    "rrp": "RRPONTSYD",         # Overnight reverse repo ($ billions, daily)
}
YF_SERIES = {
    "vix": "^VIX",
    "vix3m": "^VIX3M",
    "brent": "BZ=F",
    "wti": "CL=F",
}

FRED_BACKFILL_START = "2010-01-01"   # ≥5y history before first stamp (percentile windows)
INCREMENTAL_DAYS = 21                # trailing re-scan: heals FRED lag + weekly series

logger = DatabaseLogger()

_DDL = """
CREATE TABLE IF NOT EXISTS macro_series (
    series      VARCHAR(24) NOT NULL,
    date        DATE        NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    fetched_at  TIMESTAMP   DEFAULT NOW(),
    PRIMARY KEY (series, date)
);
CREATE INDEX IF NOT EXISTS macro_series_date_idx ON macro_series (date);
"""


def ensure_table():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()
    conn.close()


def parse_fred_csv(text):
    """fredgraph.csv → [(date, float)]. Header row skipped; '.' (missing) rows dropped."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        try:
            d = datetime.strptime(row[0].strip(), "%Y-%m-%d").date()
            out.append((d, float(row[1])))
        except ValueError:
            continue  # header line or '.' placeholder
    return out


def fetch_fred(series_id, start, retries=2):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=30,
                             headers={"User-Agent": "macro-fetcher/1.0 (research)"})
            r.raise_for_status()
            return parse_fred_csv(r.text)
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
    raise last


def fetch_yf(symbol, period):
    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return []
    out = []
    for ts, row in df.iterrows():
        try:
            out.append((ts.date(), float(row["Close"])))
        except Exception:
            continue
    return out


def save_series(name, pairs):
    """First-release-only insert: existing (series, date) rows are never touched."""
    if not pairs:
        return 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # single page → cur.rowcount is the true inserted count (paged execute_values
            # reports only the LAST page's rowcount)
            execute_values(cur, """
                INSERT INTO macro_series (series, date, value) VALUES %s
                ON CONFLICT (series, date) DO NOTHING
            """, [(name, d, v) for d, v in pairs], page_size=max(len(pairs), 1))
            n = cur.rowcount
        conn.commit()
        return max(n or 0, 0)
    except Exception as e:
        conn.rollback()
        print(f"  ✗ {name}: insert failed: {e}")
        return 0
    finally:
        conn.close()


def _series_max_date(name):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(date) FROM macro_series WHERE series=%s", (name,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def fetch_all_macro(backfill=False, verbose=True):
    """Fetch every configured series. One failing series never blocks the rest."""
    ensure_table()
    inserted, per_series = 0, {}

    for name, sid in FRED_SERIES.items():
        try:
            if backfill or _series_max_date(name) is None:
                start = FRED_BACKFILL_START
            else:
                start = (date.today() - timedelta(days=INCREMENTAL_DAYS)).isoformat()
            n = save_series(name, fetch_fred(sid, start))
        except Exception as e:
            print(f"  ✗ {name} ({sid}): FRED fetch failed: {e}")
            logger.error("MACRO_FETCH_ERROR", f"FRED fetch failed for {name}",
                         details={"series": name, "fred_id": sid, "error": str(e)})
            n = 0
        per_series[name] = n
        inserted += n

    for name, sym in YF_SERIES.items():
        try:
            period = "max" if (backfill or _series_max_date(name) is None) else "1mo"
            n = save_series(name, fetch_yf(sym, period))
        except Exception as e:
            print(f"  ✗ {name} ({sym}): yfinance fetch failed: {e}")
            logger.error("MACRO_FETCH_ERROR", f"yfinance fetch failed for {name}",
                         details={"series": name, "symbol": sym, "error": str(e)})
            n = 0
        per_series[name] = n
        inserted += n

    if verbose:
        parts = "  ".join(f"{k}:{v}" for k, v in per_series.items())
        print(f"✓ macro_series: +{inserted} rows ({parts})")
    return {"rows": inserted, "series": per_series}


def show_stats():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""SELECT series, count(*), min(date), max(date)
                       FROM macro_series GROUP BY series ORDER BY series""")
        for s, n, lo, hi in cur.fetchall():
            print(f"  {s:8s} n={n:6d}  {lo} .. {hi}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Macro/regime series fetcher (Phase 14)")
    ap.add_argument("--backfill", action="store_true", help="full history instead of incremental")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        ensure_table()
        show_stats()
    else:
        fetch_all_macro(backfill=args.backfill)
        show_stats()
