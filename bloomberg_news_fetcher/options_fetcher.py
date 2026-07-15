"""
Options implied-volatility snapshots (Modification Plan 9.4) — forward-PIT archive of
ATM IV per ticker, same pattern as fundamentals_fetcher (snapshot what yfinance shows
TODAY; history accumulates forward and can never be backfilled — chains are ephemeral).

Per ticker and snapshot date we store, for the nearest N monthly expiries: the ATM
call/put implied vols (strike nearest to spot), simple 25-delta-ish skew proxy (IV at
±10% strikes), and aggregate volume / open interest. Downstream (advisory first):
`priced_in_check` upgrade — "is the event already in the options?" via IV percentile
once enough forward history exists.

CLI:
    python options_fetcher.py                  # snapshot the universe
    python options_fetcher.py --tickers NVDA --stats
"""
import argparse
from datetime import datetime, timedelta

import pytz
import yfinance as yf
from psycopg2.extras import Json

from daily_price_fetcher import get_db_connection
from logger import DatabaseLogger
from price_fetcher import TOP_50_TICKERS

N_EXPIRIES = 3
EASTERN_TZ = pytz.timezone("America/New_York")
logger = DatabaseLogger()

_DDL = """
CREATE TABLE IF NOT EXISTS options_iv (
    ticker        VARCHAR(10) NOT NULL,
    snapshot_date DATE NOT NULL,
    data          JSONB NOT NULL,
    fetched_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (ticker, snapshot_date)
);
"""


def ensure_table():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()
    conn.close()


def _nearest_row(df, target_strike):
    if df is None or df.empty:
        return None
    row = df.iloc[(df["strike"] - target_strike).abs().argsort()].iloc[0]
    iv = row.get("impliedVolatility")
    return {"strike": float(row["strike"]), "iv": round(float(iv), 4) if iv is not None else None}


def summarize_chain(calls, puts, spot, expiry):
    """Pure: one expiry's chains → compact ATM/skew/liquidity summary."""
    out = {"expiry": expiry}
    atm_c, atm_p = _nearest_row(calls, spot), _nearest_row(puts, spot)
    out["atm_call_iv"] = atm_c["iv"] if atm_c else None
    out["atm_put_iv"] = atm_p["iv"] if atm_p else None
    up, down = _nearest_row(calls, spot * 1.10), _nearest_row(puts, spot * 0.90)
    out["call_iv_p110"] = up["iv"] if up else None
    out["put_iv_p90"] = down["iv"] if down else None
    if out["put_iv_p90"] is not None and out["atm_put_iv"] is not None:
        out["put_skew"] = round(out["put_iv_p90"] - out["atm_put_iv"], 4)
    try:
        out["call_oi"] = int(calls["openInterest"].fillna(0).sum())
        out["put_oi"] = int(puts["openInterest"].fillna(0).sum())
        out["put_call_oi"] = round(out["put_oi"] / out["call_oi"], 3) if out["call_oi"] else None
    except Exception:
        pass
    return out


def snapshot_ticker(ticker):
    """One ticker's IV snapshot dict, or None when no listed options / no spot."""
    t = yf.Ticker(ticker)
    try:
        # skip expiries within a day of the snapshot — expiry-day chains carry degenerate
        # IVs (near-zero ATM, wild wings) that would poison the forward archive
        cutoff = (datetime.now(EASTERN_TZ).date() + timedelta(days=1)).isoformat()
        expiries = [e for e in (t.options or []) if e > cutoff][:N_EXPIRIES]
        if not expiries:
            return None
        hist = t.history(period="5d", interval="1d")
        spot = float(hist["Close"].iloc[-1]) if hist is not None and not hist.empty else None
        if not spot:
            return None
    except Exception as e:
        print(f"  ⚠ {ticker}: chain listing failed: {e}")
        return None
    per_expiry = []
    for exp in expiries:
        try:
            ch = t.option_chain(exp)
            per_expiry.append(summarize_chain(ch.calls, ch.puts, spot, exp))
        except Exception as e:
            print(f"  ⚠ {ticker} {exp}: chain fetch failed: {e}")
    if not per_expiry:
        return None
    return {"spot": round(spot, 2), "expiries": per_expiry}


def fetch_all_options(tickers=None, verbose=True):
    tickers = tickers or TOP_50_TICKERS
    ensure_table()
    snap_date = datetime.now(EASTERN_TZ).date()
    conn = get_db_connection()
    n = 0
    failed = []
    for idx, tk in enumerate(tickers, 1):
        if verbose:
            print(f"[{idx}/{len(tickers)}] {tk} ...", flush=True)
        try:
            data = snapshot_ticker(tk)
            if data is None:
                continue
            with conn.cursor() as cur:
                # first-release only: an existing (ticker, date) snapshot is never rewritten
                cur.execute("""INSERT INTO options_iv (ticker, snapshot_date, data)
                               VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                            [tk.upper(), snap_date, Json(data)])
                n += cur.rowcount or 0
            conn.commit()
        except Exception as e:
            conn.rollback()
            failed.append(tk)
            print(f"  ✗ {tk}: {type(e).__name__}: {e}")
    conn.close()
    if verbose:
        print(f"✓ options_iv: +{n} snapshots ({snap_date}); failed: {failed or 'none'}")
    logger.info("OPTIONS_FETCH_COMPLETE", f"+{n} IV snapshots", details={"failed": failed})
    return {"rows": n, "failed": failed}


def show_stats():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*), min(snapshot_date), max(snapshot_date),
                              count(DISTINCT ticker) FROM options_iv""")
        n, lo, hi, tks = cur.fetchone()
        print(f"  options_iv n={n}  {lo} .. {hi}  tickers={tks}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Options ATM-IV snapshots (9.4)")
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        ensure_table()
        show_stats()
    else:
        fetch_all_options(tickers=args.tickers)
        show_stats()
