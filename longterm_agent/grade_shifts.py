"""
Grade matured shift_library rows: fill fwd_return_2w/4w/12w and the SPY benchmark return.

Entry = close on/before as_of; exit = close on/before as_of + N weeks. A horizon is gradable
only once as_of + N weeks ≤ today (the outcome window has elapsed). Excess return (stock −
benchmark) is computed in the report; here we store the raw stock returns + benchmark_return_4w.

    python -m longterm_agent.grade_shifts
"""
import argparse
from datetime import datetime, timedelta, date

import psycopg2

from longterm_agent.config import LongTermAgentConfig

BENCHMARK = "SPY"
HORIZONS = {"fwd_return_2w": 14, "fwd_return_4w": 28, "fwd_return_12w": 84}


def _close_on_or_before(cur, ticker, target: date, tolerance_days=7):
    """Latest daily close on/before target (within tolerance). Returns float or None."""
    cur.execute("""
        SELECT close FROM stock_prices_daily
        WHERE ticker=%s AND date <= %s AND date >= %s AND close IS NOT NULL
        ORDER BY date DESC LIMIT 1
    """, [ticker.upper(), target, target - timedelta(days=tolerance_days)])
    r = cur.fetchone()
    return float(r[0]) if r else None


def _ret(cur, ticker, entry_date: date, horizon_days: int):
    entry = _close_on_or_before(cur, ticker, entry_date)
    exit_ = _close_on_or_before(cur, ticker, entry_date + timedelta(days=horizon_days))
    if entry and exit_:
        return (exit_ - entry) / entry
    return None


def main():
    ap = argparse.ArgumentParser(description="Grade matured shift_library rows")
    ap.add_argument("--asof-today", default=None, help="override 'today' (YYYY-MM-DD) for testing")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    today = datetime.strptime(args.asof_today, "%Y-%m-%d").date() if args.asof_today else date.today()

    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    cur.execute("""SELECT id, ticker, as_of FROM shift_library
                   WHERE outcome_filled_at IS NULL ORDER BY as_of""")
    rows = cur.fetchall()
    print(f"{len(rows)} ungraded rows; today={today}")

    graded = 0
    for rid, ticker, as_of in rows:
        updates, vals = [], []
        for col, days in HORIZONS.items():
            if as_of + timedelta(days=days) <= today:
                r = _ret(cur, ticker, as_of, days)
                if r is not None:
                    updates.append(f"{col}=%s"); vals.append(round(r, 4))
        # benchmark for the 4w window (the primary checkpoint)
        if as_of + timedelta(days=28) <= today:
            b = _ret(cur, BENCHMARK, as_of, 28)
            if b is not None:
                updates.append("benchmark_return_4w=%s"); vals.append(round(b, 4))
        # only mark filled once the 4w (primary) horizon is gradable
        if as_of + timedelta(days=28) <= today and updates:
            updates.append("outcome_filled_at=%s"); vals.append(today)
            cur.execute(f"UPDATE shift_library SET {', '.join(updates)} WHERE id=%s", vals + [rid])
            graded += 1

    conn.commit()
    conn.close()
    print(f"Graded {graded} rows (4w horizon matured).")


if __name__ == "__main__":
    main()
