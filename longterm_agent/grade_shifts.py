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


def _has_data_through(cur, ticker, target: date, cache: dict) -> bool:
    """True if the ticker's daily series actually REACHES `target` (Phase 2 guard).

    `_close_on_or_before`'s 7-day tolerance silently grades against a FROZEN series
    (the 2026-06-15 SPY incident: exits within 7d of the freeze got the stale close).
    A weekend/holiday target resolves 1-2 days later once the next trading day lands —
    a small grading delay, never a wrong grade.
    """
    if ticker not in cache:
        cur.execute("SELECT max(date) FROM stock_prices_daily WHERE ticker=%s", [ticker.upper()])
        r = cur.fetchone()
        cache[ticker] = r[0] if r else None
    return cache[ticker] is not None and cache[ticker] >= target


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

    graded, pending = 0, 0
    max_dates = {}
    for rid, ticker, as_of in rows:
        t4 = as_of + timedelta(days=28)
        if t4 > today:
            continue  # primary horizon not matured yet
        # pending_data guard: BOTH the stock and the SPY benchmark series must reach
        # the 4w exit date before the row is filled; otherwise retry on a later run.
        if not (_has_data_through(cur, ticker, t4, max_dates)
                and _has_data_through(cur, BENCHMARK, t4, max_dates)):
            pending += 1
            continue
        updates, vals = [], []
        for col, days in HORIZONS.items():
            target = as_of + timedelta(days=days)
            if target <= today and _has_data_through(cur, ticker, target, max_dates):
                r = _ret(cur, ticker, as_of, days)
                if r is not None:
                    updates.append(f"{col}=%s"); vals.append(round(r, 4))
        # benchmark for the 4w window (the primary checkpoint)
        b = _ret(cur, BENCHMARK, as_of, 28)
        if b is not None:
            updates.append("benchmark_return_4w=%s"); vals.append(round(b, 4))
        if updates:
            updates.append("outcome_filled_at=%s"); vals.append(today)
            cur.execute(f"UPDATE shift_library SET {', '.join(updates)} WHERE id=%s", vals + [rid])
            graded += 1

    conn.commit()
    conn.close()
    print(f"Graded {graded} rows (4w horizon matured); {pending} pending_data (price series not caught up).")


if __name__ == "__main__":
    main()
