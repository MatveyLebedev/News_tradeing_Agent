#!/usr/bin/env python3
"""
Trade simulator using Yahoo Finance when local OHLC tables are missing.

Same rules as trade_simulator.py:
  entry = news_published + 1 min
  exit  = entry + 40 min

Usage:
  python profit_compute/trade_simulator_yahoo.py \\
    --start-date 2026-05-01 --end-date 2026-06-02 \\
    --agent complex_agent --min-confidence 0.7
"""
import argparse
from datetime import datetime, timedelta, time
from typing import Optional

import pandas as pd
import pytz

from trade_simulator import (
    filter_tradable_signals,
    fetch_signals,
    get_price_at_time,
    get_eod_price,
    get_table_name,
    connect_db,
    market_close_ts,
    to_eastern,
    plot_by_news,
    plot_results,
    print_summary,
    write_signals_table,
)

try:
    import yfinance as yf
except ImportError:
    yf = None

EASTERN = pytz.timezone("America/New_York")
_bar_cache: dict[tuple[str, str], pd.DataFrame] = {}


def yahoo_symbol(ticker: str) -> str:
    return ticker.upper().replace(".", "-")


def _to_eastern(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return EASTERN.localize(ts)
    return ts.astimezone(EASTERN)


def _pick_interval(entry: datetime) -> Optional[str]:
    """Yahoo intraday limits: 1m ~7d, 5m ~60d."""
    age = datetime.now(EASTERN) - _to_eastern(entry)
    if age.days <= 7:
        return "1m"
    if age.days <= 60:
        return "5m"
    return None


def _load_bars(ticker: str, day: datetime, interval: str) -> pd.DataFrame:
    sym = yahoo_symbol(ticker)
    key = (sym, day.strftime("%Y-%m-%d"), interval)
    if key in _bar_cache:
        return _bar_cache[key]

    start = day.strftime("%Y-%m-%d")
    end = (day + timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(sym, start=start, end=end, interval=interval, progress=False, auto_adjust=False)
    if df is None or df.empty:
        _bar_cache[key] = pd.DataFrame()
        return _bar_cache[key]

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    tcol = "Datetime" if "Datetime" in df.columns else "Date"
    df[tcol] = pd.to_datetime(df[tcol])
    if df[tcol].dt.tz is None:
        df[tcol] = df[tcol].dt.tz_localize(EASTERN)
    else:
        df[tcol] = df[tcol].dt.tz_convert(EASTERN)
    df = df.rename(columns={tcol: "timestamp", "Close": "close"})
    _bar_cache[key] = df[["timestamp", "close"]]
    return _bar_cache[key]


def get_yahoo_price_at_time(ticker: str, timestamp: datetime, tolerance_min: int = 5) -> Optional[float]:
    if yf is None:
        return None

    ts = _to_eastern(timestamp)
    interval = _pick_interval(ts)
    if interval is None:
        return None

    bars = _load_bars(ticker, ts.replace(hour=0, minute=0, second=0, microsecond=0), interval)
    if bars.empty:
        return None

    lo, hi = ts - timedelta(minutes=tolerance_min), ts + timedelta(minutes=tolerance_min)
    window = bars[(bars["timestamp"] >= lo) & (bars["timestamp"] <= hi)]
    if window.empty:
        return None
    window = window.copy()
    window["delta"] = (window["timestamp"] - ts).abs()
    return float(window.sort_values("delta").iloc[0]["close"])


def get_first_price_after(ticker: str, timestamp: datetime, db_first: bool, within_min: int = 30):
    if db_first:
        conn = connect_db()
        cursor = conn.cursor()
        table = get_table_name(ticker)
        hi = timestamp + timedelta(minutes=within_min)
        try:
            cursor.execute(
                f"SELECT close FROM {table} WHERE timestamp >= %s AND timestamp <= %s ORDER BY timestamp ASC LIMIT 1",
                (timestamp, hi),
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                return float(row[0]), "db"
        except Exception:
            cursor.close()
            conn.close()

    if yf is None:
        return None, "none"
    ts = _to_eastern(timestamp)
    interval = _pick_interval(ts)
    if interval is None:
        return None, "none"
    bars = _load_bars(ticker, ts.replace(hour=0, minute=0, second=0, microsecond=0), interval)
    if bars.empty:
        return None, "none"
    hi = ts + timedelta(minutes=within_min)
    window = bars[(bars["timestamp"] >= ts) & (bars["timestamp"] <= hi)]
    if window.empty:
        return None, "none"
    return float(window.iloc[0]["close"]), "yahoo"


def get_price(ticker: str, timestamp: datetime, db_first: bool) -> tuple[Optional[float], str]:
    if db_first:
        p = get_price_at_time(ticker, timestamp)
        if p is not None:
            return p, "db"
    p = get_yahoo_price_at_time(ticker, timestamp)
    return (p, "yahoo") if p is not None else (None, "none")


def get_yahoo_eod_price(ticker: str, day) -> Optional[float]:
    if yf is None:
        return None
    if hasattr(day, "year") and not hasattr(day, "hour"):
        day_dt = EASTERN.localize(datetime.combine(day, time(12)))
    else:
        day_dt = to_eastern(day if isinstance(day, datetime) else datetime.combine(day, time(12)))
    close_ts = market_close_ts(day_dt)
    for interval in ("5m", "1m"):
        if _pick_interval(day_dt) is None and interval == "1m":
            continue
        bars = _load_bars(ticker, day_dt.replace(hour=0, minute=0, second=0, microsecond=0), interval)
        if bars.empty:
            continue
        day_bars = bars[bars["timestamp"] <= close_ts]
        if not day_bars.empty:
            return float(day_bars.iloc[-1]["close"])
    return None


def resolve_exit(ticker: str, entry_time: datetime, hold_minutes: int, db_first: bool):
    target = entry_time + timedelta(minutes=hold_minutes)
    eod = market_close_ts(entry_time)
    exit_time = min(target, eod)
    exit_price, exit_src = get_price(ticker, exit_time, db_first)
    if exit_price is not None:
        return exit_time, exit_price, exit_src

    day = to_eastern(entry_time).date()
    if db_first:
        p = get_eod_price(ticker, day)
        if p is not None:
            return eod, p, "db_eod"
    p = get_yahoo_eod_price(ticker, day)
    if p is not None:
        return eod, p, "yahoo_eod"
    return exit_time, None, "none"


def simulate_trades_yahoo(signals: list, db_first: bool = True, hold_minutes: int = 40) -> pd.DataFrame:
    trades = []
    skipped = {"no_ticker": 0, "no_price": 0, "yahoo_too_old": 0}
    eod_exits = 0

    for sig in signals:
        ticker = sig.get("ticker")
        if not ticker:
            skipped["no_ticker"] += 1
            continue

        entry_time = sig["news_published"] + timedelta(minutes=1)

        if _pick_interval(entry_time) is None and not (
            db_first and get_price_at_time(ticker, entry_time) is not None
        ):
            skipped["yahoo_too_old"] += 1
            continue

        entry_price, entry_src = get_price(ticker, entry_time, db_first)
        if entry_price is None:
            entry_price, entry_src = get_first_price_after(ticker, entry_time, db_first)
        exit_time, exit_price, exit_src = resolve_exit(ticker, entry_time, hold_minutes, db_first)
        if entry_price is None or exit_price is None:
            skipped["no_price"] += 1
            continue
        if exit_src in ("db_eod", "yahoo_eod") or exit_time == market_close_ts(entry_time):
            eod_exits += 1

        if sig["signal"] == "buy":
            profit_pct = (exit_price - entry_price) / entry_price * 100
        else:
            profit_pct = (entry_price - exit_price) / entry_price * 100

        trades.append(
            {
                "news_id": sig["news_id"],
                "news_title": sig["news_title"],
                "ticker": ticker,
                "signal": sig["signal"],
                "confidence": sig["confidence"],
                "agent": sig["agent_name"],
                "entry_time": entry_time,
                "exit_time": exit_time,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_src": entry_src,
                "exit_src": exit_src,
                "profit_pct": profit_pct,
                "win": profit_pct > 0,
            }
        )

    print(
        f"Skipped: no_ticker={skipped['no_ticker']}, "
        f"no_price={skipped['no_price']}, yahoo_window={skipped['yahoo_too_old']}"
    )
    print(f"EOD exits (capped or fallback): {eod_exits}/{len(trades) + skipped['no_price'] + skipped['yahoo_too_old']}")
    return pd.DataFrame(trades)


def run_yahoo_simulation(
    start_date: str,
    end_date: str,
    agent: Optional[str],
    min_confidence: float,
    run_after: Optional[str] = None,
    agent_version: Optional[str] = "2.0.0",
    db_first: bool = True,
    hold_minutes: int = 40,
):
    print(f"\nYahoo trade simulator")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Agent: {agent or 'all'}")
    print(f"Agent version: {agent_version or 'all'}")
    print(f"Min confidence: {min_confidence}")
    print(f"Hold time: {hold_minutes} min")
    print(f"Price source: {'DB then Yahoo' if db_first else 'Yahoo only'}")

    signals = fetch_signals(
        start_date, end_date, agent, min_confidence,
        run_after=run_after, agent_version=agent_version,
    )
    tradable, pre_market = filter_tradable_signals(signals)
    print(f"Found {len(signals)} signals ({len(pre_market)} before 9:30 ET excluded)")

    # Table: all signals in range (any confidence), with publish time
    all_signals = fetch_signals(
        start_date, end_date, agent, min_confidence=0.0,
        run_after=run_after, agent_version=agent_version,
    )
    
    if not tradable:
        if signals:
            write_signals_table(all_signals or signals, None, min_confidence=min_confidence)
        return

    print("\nSimulating trades...")
    df = simulate_trades_yahoo(tradable, db_first=db_first, hold_minutes=hold_minutes)
    if not df.empty:
        src = df.groupby(["entry_src", "exit_src"]).size()
        print("Price sources (entry/exit counts):", src.to_dict())

    print_summary(df)
    write_signals_table(all_signals or signals, df, min_confidence=min_confidence)
    plot_results(df)
    plot_by_news(df, output_name="simulation_by_news_yahoo.png")


def main():
    if yf is None:
        raise SystemExit("Install yfinance: pip install yfinance")

    parser = argparse.ArgumentParser(description="Simulate trades using Yahoo Finance prices")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--run-after", default=None)
    parser.add_argument("--agent-version", default="2.0.0",
                        help="Filter by agent_version (default: 2.0.0)")
    parser.add_argument("--yahoo-only", action="store_true", help="Skip DB, use Yahoo only")
    parser.add_argument("--hold-minutes", type=int, default=40,
                        help="Minutes to hold after entry (default: 40, use 180 for 3h)")
    args = parser.parse_args()

    run_yahoo_simulation(
        args.start_date,
        args.end_date,
        args.agent,
        args.min_confidence,
        run_after=args.run_after,
        agent_version=args.agent_version,
        db_first=not args.yahoo_only,
        hold_minutes=args.hold_minutes,
    )


if __name__ == "__main__":
    main()
