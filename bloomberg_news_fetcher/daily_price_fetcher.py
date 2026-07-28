"""
Daily OHLCV fetcher — fills `stock_prices_daily` for the long-term agent.

Backfills ~2 years of daily bars from yfinance and appends incrementally on later runs
(idempotent upsert on (ticker, date)). Same "fetchers write, agents read" pattern and
conventions as price_fetcher.py. Runs on the weekly fundamentals cadence (off-market).

CLI:
    python daily_price_fetcher.py                 # backfill 2y for the universe
    python daily_price_fetcher.py --period 5d     # incremental top-up
    python daily_price_fetcher.py --tickers AAPL NVDA --stats
"""
import math
import os
import time

import pytz
import yfinance as yf
import psycopg2
from psycopg2.extras import execute_values

from config import DB_CONFIG
from logger import DatabaseLogger
from price_fetcher import TOP_50_TICKERS

# SPY is the grading benchmark (grade_results: excess return vs SPY), NOT part of the
# agent universe — TOP_50_TICKERS feeds LongTermAgentConfig.get_universe(), so it must
# stay agent-analyzable companies only. Any default fetch has to include BOTH lists:
# passing bare TOP_50_TICKERS froze SPY at 2026-06-15 and blocked all outcome grading.
# Sector ETFs (Phase 11): sector-relative grading benchmarks — mirrors
# longterm_agent/sector_map.py SECTOR_ETF (kept literal here: the fetcher container
# doesn't ship the longterm package).
BENCHMARK_TICKERS = ["SPY", "XLB", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI",
                     "XLRE", "XLK", "XLU"]
DEFAULT_TICKERS = TOP_50_TICKERS + BENCHMARK_TICKERS

logger = DatabaseLogger()
EASTERN_TZ = pytz.timezone("America/New_York")


def get_db_connection(retries=3, delay=2.0):
    # One connection per ticker means one transient network drop used to kill the whole
    # multi-ticker run mid-way; retry with backoff so a flaky link costs seconds, not the run.
    last = None
    for attempt in range(retries):
        try:
            return psycopg2.connect(
                host=DB_CONFIG["host"], database=DB_CONFIG["database"],
                user=DB_CONFIG["user"], password=DB_CONFIG["password"], port=DB_CONFIG["port"],
                connect_timeout=10,
            )
        except psycopg2.OperationalError as e:
            last = e
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last


def ensure_table():
    sql_path = os.path.join(os.path.dirname(__file__), "create_daily_price_table.sql")
    with open(sql_path) as f:
        ddl = f.read()
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    conn.close()


def fetch_daily(ticker, period="2y"):
    """Fetch daily bars for one ticker as a DataFrame, or None."""
    try:
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
        return df if df is not None and not df.empty else None
    except Exception as e:
        print(f"  ✗ {ticker}: daily fetch failed: {e}")
        logger.error("DAILY_FETCH_ERROR", f"Daily fetch failed for {ticker}",
                     details={"ticker": ticker, "error": str(e)})
        return None


def _finite(x):
    """The value as a float, or None if it is missing / NaN / inf.

    yfinance hands back a bar whose OHLC are present but Close is NaN when the session
    has not finalised on Yahoo's side yet. `float(nan)` raises nothing and Postgres
    `numeric` stores NaN happily, so an unguarded write puts a poison value in the price
    table — see the 2026-07-24 incident: 131 tickers written with close=NaN on the
    Saturday top-up, which then crashed 49 of 64 tickers in the Sunday batch.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def save_daily(ticker, df):
    if df is None or df.empty:
        return 0
    rows, dropped = [], 0
    for ts, row in df.iterrows():
        try:
            o, h, lo, c = (_finite(row.get(k)) for k in ("Open", "High", "Low", "Close"))
            adj = _finite(row.get("Adj Close"))
            volume = int(row["Volume"])
        except Exception:
            dropped += 1
            continue
        # A bar is written whole or not at all: a partial row would silently pass the
        # `close IS NOT NULL` checks every reader makes. A missing date is loud (the
        # freshness preflight sees the gap); a NaN close is not.
        if None in (o, h, lo, c):
            dropped += 1
            continue
        rows.append((ticker.upper(), ts.date(), o, h, lo, c,
                     adj if adj is not None else c, volume))
    if dropped:
        print(f"  ⚠ {ticker}: dropped {dropped} bar(s) with non-finite OHLC")
        logger.warning("DAILY_BAR_DROPPED", f"{ticker}: {dropped} non-finite bar(s) skipped",
                       details={"ticker": ticker, "dropped": dropped})
    if not rows:
        return 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO stock_prices_daily (ticker, date, open, high, low, close, adj_close, volume)
                VALUES %s
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                    close=EXCLUDED.close, adj_close=EXCLUDED.adj_close, volume=EXCLUDED.volume
            """, rows)
            n = cur.rowcount
        conn.commit()
        return n if n and n > 0 else len(rows)
    except Exception as e:
        conn.rollback()
        print(f"  ✗ {ticker}: daily insert failed: {e}")
        return 0
    finally:
        conn.close()


def fetch_all_daily(tickers=None, period="2y", verbose=True):
    if tickers is None:
        tickers = DEFAULT_TICKERS
    ensure_table()
    results = {"tickers": len(tickers), "rows": 0, "failed": []}
    logger.info("DAILY_FETCH_START", f"Daily price fetch for {len(tickers)} tickers ({period})")
    for idx, ticker in enumerate(tickers, 1):
        if verbose:
            print(f"[{idx}/{len(tickers)}] {ticker} ...", flush=True)
        df = fetch_daily(ticker, period=period)
        n = save_daily(ticker, df)
        results["rows"] += n
        if n == 0:
            results["failed"].append(ticker)
    if verbose:
        print(f"\nDaily fetch complete: {results['rows']} rows, {len(results['failed'])} failed {results['failed'] or ''}")
    logger.info("DAILY_FETCH_COMPLETE", f"Daily fetch: {results['rows']} rows", details=results)
    return results


def show_stats():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT ticker), min(date), max(date) FROM stock_prices_daily")
        total, tickers, dmin, dmax = cur.fetchone()
    conn.close()
    print(f"\nstock_prices_daily: {total} rows, {tickers} tickers, {dmin} → {dmax}\n")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Fetch daily OHLCV bars into stock_prices_daily")
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--period", default="2y", help="yfinance period (2y backfill, 5d top-up)")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.stats:
        show_stats()
    else:
        fetch_all_daily(tickers=args.tickers, period=args.period, verbose=not args.quiet)
