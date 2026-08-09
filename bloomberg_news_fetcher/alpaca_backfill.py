#!/usr/bin/env python3
"""Backfill 1-minute bars from Alpaca into the per-ticker minute tables.

Why this source. Yahoo — what `price_fetcher.py` uses — serves ~30 days of minute history
and 8 days per request, so a hole not repaired inside that window is permanent
(docs/PREREG_MONDAY_MIDDAY_HOLDING_2026-07-29.md, which lost April-June to exactly this).
Alpaca's FREE tier reaches the full SIP feed for anything older than 15 minutes, 7+ years
deep, on a paper-only signup with no funded account. So a window that closed months ago
costs nothing to recover.

Two things this must get right, and both are silent when wrong:

  * TIME ZONE. Alpaca stamps bars UTC; this repo stores US Eastern everywhere. A UTC row
    reaching these tables is 4-5 hours out of line with every other source and nothing
    downstream would notice.
  * SESSION. Alpaca returns the extended session (04:00-20:00 ET). Yahoo put only the
    regular session in these tables, so keeping the extra bars would make the backfilled
    window structurally different from the live one — same table, two definitions of a day.
    Regular hours only unless --extended.

Rows carry `source='alpaca'`. The column is added to any table this touches, because a
minute table that mixes vendors without saying so cannot be PIT-audited afterwards.

    ALPACA_API_KEY_ID=... ALPACA_SECRET_KEY=... \
    python3 -m bloomberg_news_fetcher.alpaca_backfill --start 2026-04-01 --end 2026-08-10
"""
import os
import sys
import time
import json
import argparse
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
PAGE_LIMIT = 10_000
RATE_PAUSE_S = 0.35              # free tier allows 200 req/min; ~170 leaves headroom
ET = ZoneInfo("America/New_York")
REGULAR_OPEN, REGULAR_CLOSE = dtime(9, 30), dtime(16, 0)

# SPY plus the 11 sector ETFs. Kept OUT of TOP_50_TICKERS upstream so the agent is never
# asked for a news background on an ETF; the minute fetcher needs both, which is the split
# commit 50f1e57 fixed.
BENCHMARKS = ["SPY", "XLB", "XLC", "XLY", "XLP", "XLE",
              "XLF", "XLV", "XLI", "XLRE", "XLK", "XLU"]


def check_end_date(end: str, today=None) -> None:
    """The free tier rejects a range ending inside the CURRENT UTC day.

    Alpaca's docs describe the limit as "end must be at least 15 minutes in the past", but
    measured 2026-08-10 the boundary is coarser: `end=2026-08-10` (midnight UTC today, i.e.
    20:00 ET yesterday and ten hours old) returns 403 "subscription does not permit querying
    recent SIP data", while `end=2026-08-09` succeeds. Checked here rather than discovered
    on page one of a long run.
    """
    today = today or datetime.now(timezone.utc).date()
    if datetime.strptime(end, "%Y-%m-%d").date() > today:
        sys.exit(f"--end {end} reaches into the current UTC day, which the free SIP tier "
                 f"refuses; use {today.isoformat()} or earlier (end is exclusive).")


def credentials():
    key = (os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_KEY_ID")
           or os.getenv("ALPACA_API_KEY"))
    secret = (os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_SECRET")
              or (os.getenv("ALPACA_API_KEY") if os.getenv("ALPACA_API_KEY_ID") else None))
    if not (key and secret):
        sys.exit("ALPACA_API_KEY_ID / ALPACA_SECRET_KEY are not set.")
    return key, secret


def table_name(ticker: str) -> str:
    """Mirrors price_fetcher.get_table_name — one naming rule, not two."""
    return "historical_data_" + ticker.lower().replace("-", "_").replace(".", "_")


def alpaca_symbol(ticker: str) -> str:
    """This repo's ticker (Yahoo convention) -> Alpaca's.

    Share classes are the whole difference: Yahoo writes BRK-B, Alpaca wants BRK.B. Not
    cosmetic — ONE unknown symbol makes the multi-symbol request return
    `400 invalid symbol` for the entire batch, so a single stale ticker silently costs all
    121 of them (measured: a run for 2026-06-24 wrote nothing at all because of BRK-B).
    """
    return ticker.upper().replace("-", ".")


def ensure_table(cur, ticker: str) -> None:
    t = table_name(ticker)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {t} (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL UNIQUE,
            open DECIMAL(12, 4), high DECIMAL(12, 4),
            low DECIMAL(12, 4),  close DECIMAL(12, 4),
            volume BIGINT,
            fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
    # Provenance. Existing Yahoo rows stay NULL, which reads as "the original vendor" —
    # backfilling a value for them would be inventing a fact.
    cur.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS source TEXT")


def fetch_page(symbols, start, end, headers, token=None):
    q = {"symbols": ",".join(symbols), "timeframe": "1Min",
         "start": f"{start}T00:00:00Z", "end": f"{end}T00:00:00Z",
         "limit": PAGE_LIMIT, "adjustment": "raw", "feed": "sip", "sort": "asc"}
    if token:
        q["page_token"] = token
    req = urllib.request.Request(f"{BARS_URL}?{urllib.parse.urlencode(q)}", headers=headers)
    with urllib.request.urlopen(req, timeout=90) as r:
        payload = json.load(r)
    return payload.get("bars") or {}, payload.get("next_page_token")


def to_et(iso_utc: str) -> datetime:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(ET)


def backfill(conn, symbols, start, end, extended=False):
    key, secret = credentials()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    with conn.cursor() as cur:
        for s in symbols:
            ensure_table(cur, s)
    conn.commit()

    counts = {s: 0 for s in symbols}
    # Request under Alpaca's names, write under ours: the response is keyed by whatever was
    # asked for, and the table name derives from the repo ticker.
    to_repo = {alpaca_symbol(s): s for s in symbols}
    token, pages = None, 0
    while True:
        try:
            bars, token = fetch_page(list(to_repo), start, end, headers, token)
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
            break
        pages += 1
        with conn.cursor() as cur:
            for wire_sym, rows in bars.items():
                sym = to_repo.get(wire_sym, wire_sym)
                vals = []
                for b in rows:
                    ts = to_et(b["t"]).replace(tzinfo=None)
                    if not extended and not (REGULAR_OPEN <= ts.time() < REGULAR_CLOSE):
                        continue
                    vals.append((ts, b["o"], b["h"], b["l"], b["c"], b["v"], "alpaca"))
                if not vals:
                    continue
                # ON CONFLICT so a re-run is idempotent and never fights the live fetcher
                # for a minute it already wrote.
                execute_values(cur, f"""
                    INSERT INTO {table_name(sym)}
                        (timestamp, open, high, low, close, volume, source)
                    VALUES %s ON CONFLICT (timestamp) DO NOTHING""", vals)
                counts[sym] += len(vals)
        conn.commit()
        print(f"  page {pages:>3}: {sum(len(v) for v in bars.values()):>6} bars"
              + ("" if token else "   (last)"))
        if not token:
            break
        time.sleep(RATE_PAUSE_S)
    return counts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument("--end", required=True,
                   help="YYYY-MM-DD, exclusive; must be an earlier UTC day than today")
    p.add_argument("--symbols", help="comma-separated; default = SPY + 11 sector ETFs")
    p.add_argument("--extended", action="store_true",
                   help="keep pre/post-market bars (default: 09:30-16:00 ET, matching Yahoo)")
    a = p.parse_args()
    check_end_date(a.end)
    syms = a.symbols.split(",") if a.symbols else BENCHMARKS

    from longterm_agent.config import LongTermAgentConfig
    conn = psycopg2.connect(**LongTermAgentConfig().get_db_config())
    print(f"{len(syms)} symbols · 1Min · SIP · ET · "
          f"{'extended' if a.extended else 'regular session'} · {a.start} → {a.end}")
    counts = backfill(conn, syms, a.start, a.end, a.extended)
    print()
    for s in syms:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), min(timestamp), max(timestamp) "
                        f"FROM {table_name(s)} WHERE source='alpaca'")
            n, lo, hi = cur.fetchone()
        print(f"  {s:<6} +{counts[s]:>7} this run · {n:>7} alpaca rows · {lo} … {hi}")
    conn.close()


if __name__ == "__main__":
    main()
