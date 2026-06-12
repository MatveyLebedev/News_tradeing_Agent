#!/usr/bin/env python3
"""
Confidence calibration: does higher agent confidence => higher win-rate / P/L?

Buckets executed trades by signal confidence and reports win-rate and average P/L
per bucket, so you can pick a profit-positive confidence threshold for trading.

Uses a single reused DB connection for speed (the main simulator opens one
connection per price lookup, which is slow against a remote DB).

Usage:
  python profit_compute/calibrate_confidence.py \
    --start-date 2026-01-01 --end-date 2026-06-02 --agent complex_agent
"""
import os
import json
import argparse
from pathlib import Path
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

import psycopg2

DB = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "trade_agent"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432"),
}
BUCKETS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def main():
    ap = argparse.ArgumentParser(description="Calibrate agent confidence vs realized win-rate")
    ap.add_argument("--start-date", default="2026-01-01")
    ap.add_argument("--end-date", default="2026-06-02")
    ap.add_argument("--agent", default="complex_agent")
    args = ap.parse_args()

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'historical_data_%'"
    )
    tables = {r[0] for r in cur.fetchall()}

    def tbl(t):
        return f"historical_data_{t.lower().replace('-', '_').replace('.', '_')}"

    def price_at(t, ts):
        name = tbl(t)
        if name not in tables:
            return None
        cur.execute(
            f"SELECT close FROM {name} WHERE timestamp BETWEEN %s AND %s "
            f"ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp-%s))) LIMIT 1",
            (ts - timedelta(minutes=5), ts + timedelta(minutes=5), ts),
        )
        r = cur.fetchone()
        return float(r[0]) if r else None

    cur.execute(
        """SELECT news_published, signals_json FROM agent_results
           WHERE agent_name=%s AND signals_generated>0
             AND news_published >= %s AND news_published < %s""",
        (args.agent, args.start_date, args.end_date),
    )
    trades = []  # (confidence, profit_pct)
    for pub, sj in cur.fetchall():
        for s in (json.loads(sj) if isinstance(sj, str) else sj) or []:
            tk = s.get("ticker")
            sig = (s.get("signal") or "").lower()
            conf = s.get("confidence", 0)
            if not tk or sig not in ("buy", "sell"):
                continue
            e = pub + timedelta(minutes=1)
            x = e + timedelta(minutes=40)
            ep, xp = price_at(tk, e), price_at(tk, x)
            if ep is None or xp is None:
                continue
            p = (xp - ep) / ep * 100 if sig == "buy" else (ep - xp) / ep * 100
            trades.append((conf, p))
    conn.close()

    print(f"\nCalibration on {len(trades)} executed trades ({args.agent}, {args.start_date}..{args.end_date})")
    print(f"{'conf bucket':>12} | {'n':>4} | {'win%':>6} | {'avg P/L%':>9} | {'total%':>8}")
    print("-" * 52)
    for lo, hi in BUCKETS:
        b = [p for c, p in trades if lo <= c < hi]
        if not b:
            continue
        wins = sum(1 for p in b if p > 0)
        print(f"{f'[{lo:.1f},{hi:.2f})':>12} | {len(b):>4} | {100*wins/len(b):>5.1f}% | "
              f"{sum(b)/len(b):>+8.3f} | {sum(b):>+7.2f}")

    if trades:
        print("\nCumulative (signals with confidence >= threshold):")
        for thr in (0.5, 0.6, 0.7, 0.8, 0.9):
            b = [p for c, p in trades if c >= thr]
            if not b:
                continue
            wins = sum(1 for p in b if p > 0)
            print(f"  >= {thr:.1f}: n={len(b):>3}  win={100*wins/len(b):>5.1f}%  "
                  f"avg={sum(b)/len(b):>+.3f}%  total={sum(b):>+.2f}%")


if __name__ == "__main__":
    main()
