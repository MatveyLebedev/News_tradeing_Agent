"""
Grade matured longterm_agent_results rows: fill fwd_return_4w + benchmark_return_4w once the
4-week horizon has elapsed. This is what gives the Telegram bot a trade win-rate. Mirrors
grade_shifts.py (which grades the shift_library); both read stock_prices_daily and use SPY
as the benchmark. Decisions used data ≤ as_of; only the *outcome* is measured later.

    python -m longterm_agent.grade_results
"""
import sys
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from longterm_agent.config import LongTermAgentConfig
from longterm_agent.grade_shifts import _ret  # reuse the close-on-or-before return helper

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

    graded = 0
    for rid, ticker, as_of in rows:
        if as_of + timedelta(days=HORIZON_DAYS) > today:
            continue  # horizon not matured yet
        r = _ret(cur, ticker, as_of, HORIZON_DAYS)
        b = _ret(cur, BENCHMARK, as_of, HORIZON_DAYS)
        if r is None:
            continue  # missing price data; try again next run
        cur.execute("""
            UPDATE longterm_agent_results
            SET fwd_return_4w=%s, benchmark_return_4w=%s, outcome_filled_at=%s WHERE id=%s
        """, [round(r, 4), round(b, 4) if b is not None else None, today, rid])
        graded += 1

    conn.commit()
    conn.close()
    print(f"Graded {graded} rows (4w horizon matured).")
    return graded


if __name__ == "__main__":
    main()
