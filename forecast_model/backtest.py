#!/usr/bin/env python3
"""
Backtesting script.

For each of the N most recent news articles:
  1. Run NewsForecastPredictor → predicted tickers + direction
  2. Look up actual 40-min price change for each predicted ticker
  3. Simulate P&L (long if "up", short if "down", skip if "neutral")
  4. Print per-news results + aggregated totals

Usage:
    python backtest.py --n 20
    python backtest.py --n 10 --unit-size 1000   # USD per trade (default 1000)
"""
import argparse
import sys
from datetime import timedelta

import psycopg2

from config import DB_CONFIG
from predictor import NewsForecastPredictor

PRICE_WINDOW_MIN = 40


def get_last_news(conn, n: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, published
            FROM news
            WHERE embedding IS NOT NULL AND published IS NOT NULL
            ORDER BY published DESC
            LIMIT %s
            """,
            (n,),
        )
        rows = cur.fetchall()
    return [{"id": r[0], "title": r[1] or "", "summary": r[2] or "", "published": r[3]} for r in rows]


def get_actual_price_change(conn, ticker: str, published) -> float | None:
    """Return actual % price change 40 min after publication."""
    t_end = published + timedelta(minutes=PRICE_WINDOW_MIN + 5)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT timestamp, close FROM stock_prices
            WHERE ticker = %s AND timestamp BETWEEN %s AND %s
            ORDER BY timestamp ASC
            """,
            (ticker, published, t_end),
        )
        rows = cur.fetchall()
    if len(rows) < 2:
        return None
    price_at_pub = float(rows[0][1])
    target = published + timedelta(minutes=PRICE_WINDOW_MIN)
    closest = min(rows, key=lambda r: abs((r[0] - target).total_seconds()))
    price_40m = float(closest[1])
    if price_at_pub == 0:
        return None
    return round((price_40m - price_at_pub) / price_at_pub * 100, 4)


def simulate_pnl(direction: str, pct_change: float, unit_size: float) -> float:
    """Simulate dollar P&L for a unit_size position."""
    if direction == "neutral":
        return 0.0
    # long on "up", short on "down"
    signed = pct_change if direction == "up" else -pct_change
    return round(signed / 100 * unit_size, 4)


def main():
    parser = argparse.ArgumentParser(description="Backtest the forecast model on N recent news")
    parser.add_argument("--n", type=int, default=10, help="Number of recent news to test (default: 10)")
    parser.add_argument("--unit-size", type=float, default=1000.0, help="USD per trade (default: 1000)")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    predictor = NewsForecastPredictor()

    try:
        news_list = get_last_news(conn, args.n)
        if not news_list:
            print("No news with embeddings found in DB.")
            sys.exit(1)

        print(f"Backtesting on {len(news_list)} news articles (unit size: ${args.unit_size:,.0f})\n")
        print("=" * 90)

        total_pnl = 0.0
        total_trades = 0
        wins = 0
        rows_out = []

        for i, news in enumerate(news_list, 1):
            text = f"{news['title']}\n\n{news['summary']}"
            print(f"[{i}/{len(news_list)}] {news['title'][:70]}...")

            try:
                result = predictor.predict(text)
            except Exception as e:
                print(f"  ERROR running predictor: {e}\n")
                continue

            direction = result.price_direction
            tickers = result.predicted_tickers

            if not tickers or direction == "neutral":
                print(f"  Direction: {direction.upper()} | No trade (neutral or no tickers)\n")
                rows_out.append({
                    "title": news["title"][:60],
                    "published": news["published"],
                    "direction": direction,
                    "tickers": [],
                    "actual_changes": {},
                    "pnl": 0.0,
                })
                continue

            actual_changes: dict[str, float | None] = {}
            trade_pnl = 0.0
            for ticker in tickers:
                chg = get_actual_price_change(conn, ticker, news["published"])
                actual_changes[ticker] = chg
                if chg is not None:
                    pnl = simulate_pnl(direction, chg, args.unit_size)
                    trade_pnl += pnl
                    total_trades += 1
                    if pnl > 0:
                        wins += 1

            total_pnl += trade_pnl

            changes_str = ", ".join(
                f"{t}: {v:+.2f}%" if v is not None else f"{t}: no data"
                for t, v in actual_changes.items()
            )
            print(f"  Direction: {direction.upper()} | Confidence: {result.confidence:.0%}")
            print(f"  Tickers  : {', '.join(tickers)}")
            print(f"  Actual   : {changes_str}")
            print(f"  Trade P&L: ${trade_pnl:+.2f}\n")

            rows_out.append({
                "title": news["title"][:60],
                "published": news["published"],
                "direction": direction,
                "tickers": tickers,
                "actual_changes": actual_changes,
                "pnl": trade_pnl,
            })

        print("=" * 90)
        print("SUMMARY")
        print("=" * 90)
        print(f"News processed : {len(rows_out)}")
        print(f"Trades executed: {total_trades}")
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        print(f"Win rate       : {win_rate:.1f}%  ({wins}/{total_trades})")
        print(f"Total P&L      : ${total_pnl:+,.2f}")
        avg = total_pnl / total_trades if total_trades > 0 else 0
        print(f"Avg P&L/trade  : ${avg:+.2f}")

    finally:
        predictor.close()
        conn.close()


if __name__ == "__main__":
    main()
