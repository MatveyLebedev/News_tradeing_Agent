"""
Grade matured longterm_agent_results rows: fill fwd_return_4w + benchmark_return_4w once the
4-week horizon has elapsed. This is what gives the Telegram bot a trade win-rate. Mirrors
grade_shifts.py (which grades the shift_library); both read stock_prices_daily and use SPY
as the benchmark. Decisions used data ≤ as_of; only the *outcome* is measured later.

    python -m longterm_agent.grade_results
"""
import argparse
from datetime import datetime, timedelta, date

import psycopg2

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.grade_shifts import _ret, _has_data_through  # shared grading helpers

BENCHMARK = "SPY"
HORIZON_DAYS = 28  # 4 weeks (the primary checkpoint)


def main():
    ap = argparse.ArgumentParser(description="Grade matured longterm_agent_results rows (4w vs SPY)")
    ap.add_argument("--asof-today", default=None, help="override 'today' (YYYY-MM-DD) for testing")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    today = datetime.strptime(args.asof_today, "%Y-%m-%d").date() if args.asof_today else date.today()

    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ticker, as_of FROM longterm_agent_results
        WHERE agent_version=%s AND verdict IN ('LONG','SHORT') AND outcome_filled_at IS NULL
        ORDER BY as_of
    """, [cfg.AGENT_VERSION])
    rows = cur.fetchall()
    print(f"{len(rows)} ungraded directional rows; today={today}")

    graded, pending = 0, 0
    max_dates = {}
    for rid, ticker, as_of in rows:
        target = as_of + timedelta(days=HORIZON_DAYS)
        if target > today:
            continue  # horizon not matured yet
        # pending_data guard (Phase 2): both series must actually reach the exit date,
        # and the benchmark is REQUIRED — the trade's score IS excess-vs-SPY, so a row
        # graded without it could never be scored. Ungraded rows retry next run.
        if not (_has_data_through(cur, ticker, target, max_dates)
                and _has_data_through(cur, BENCHMARK, target, max_dates)):
            pending += 1
            continue
        r = _ret(cur, ticker, as_of, HORIZON_DAYS)
        b = _ret(cur, BENCHMARK, as_of, HORIZON_DAYS)
        if r is None or b is None:
            pending += 1
            continue
        cur.execute("""
            UPDATE longterm_agent_results
            SET fwd_return_4w=%s, benchmark_return_4w=%s, outcome_filled_at=%s WHERE id=%s
        """, [round(r, 4), round(b, 4), today, rid])
        graded += 1

    conn.commit()
    conn.close()
    print(f"Graded {graded} rows (4w horizon matured); {pending} pending_data (price series not caught up).")
    return graded


if __name__ == "__main__":
    main()
