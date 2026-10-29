"""
Grade matured shift_library rows: fill fwd_return_2w/4w/12w and the SPY benchmark return.

Fill convention `open_t1` (exit_rules): entry = open of the first bar strictly after
as_of; exit = close of the last session on/before entry + N weeks. A horizon is
gradable only once entry + N weeks ≤ today (the outcome window has elapsed) AND that
exit session's own bar is in the table. Excess return (stock −
benchmark) is computed in the report; here we store the raw stock returns +
benchmark_return_4w. The library shares the trades' convention so analog base rates
measure the same tradable quantity.

    python -m longterm_agent.grade_shifts
"""
import argparse
from datetime import datetime, timedelta, date
from math import isfinite

import psycopg2

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.exit_rules import (FILL_CONVENTION, close_on_or_before, entry_bar,
                                       has_exit_bar, t1_return)

BENCHMARK = "SPY"
HORIZONS = {"fwd_return_2w": 14, "fwd_return_4w": 28, "fwd_return_12w": 84}

_close_on_or_before = close_on_or_before  # back-compat alias for existing importers
# The shared grading guard (grade_results, regrade_fill_convention import it under this
# name). It used to compare the series' MAX date with the target, which passed when the
# exit bar itself was missing but a later one had landed; see exit_rules.has_exit_bar.
_has_data_through = has_exit_bar


def _ret(cur, ticker, entry_date: date, horizon_days: int):
    """LEGACY close→close return (still correct for TRAILING windows, e.g. the regime
    classifier). Grading paths use exit_rules.t1_return — do not grade with this."""
    entry = close_on_or_before(cur, ticker, entry_date)
    exit_ = close_on_or_before(cur, ticker, entry_date + timedelta(days=horizon_days))
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
    cur.execute("""ALTER TABLE shift_library
                   ADD COLUMN IF NOT EXISTS entry_date DATE,
                   ADD COLUMN IF NOT EXISTS entry_px REAL,
                   ADD COLUMN IF NOT EXISTS fill_convention VARCHAR(12)""")
    cur.execute("""SELECT id, ticker, as_of FROM shift_library
                   WHERE outcome_filled_at IS NULL ORDER BY as_of""")
    rows = cur.fetchall()
    print(f"{len(rows)} ungraded rows; today={today}")

    graded, pending = 0, 0
    exit_bars = {}
    for rid, ticker, as_of in rows:
        eb = entry_bar(cur, ticker, as_of)
        if eb is None:
            pending += 1  # entry session hasn't landed in stock_prices_daily yet
            continue
        e_date, e_px = eb
        t4 = e_date + timedelta(days=28)
        if t4 > today:
            continue  # primary horizon not matured yet
        # pending_data guard: BOTH the stock and the SPY benchmark must have the 4w exit
        # session's own bar before the row is filled; otherwise retry on a later run.
        if not (_has_data_through(cur, ticker, t4, exit_bars, today)
                and _has_data_through(cur, BENCHMARK, t4, exit_bars, today)):
            pending += 1
            continue
        updates, vals = [], []
        nonfinite = False
        for col, days in HORIZONS.items():
            target = e_date + timedelta(days=days)
            if target <= today and _has_data_through(cur, ticker, target, exit_bars, today):
                r = t1_return(cur, ticker, as_of, days)
                if r is not None:
                    # `round(nan, 4)` is nan — it does NOT raise. A defective bar therefore
                    # grades to a NaN outcome that passes every `IS NOT NULL` filter
                    # downstream and poisons the analog medians (2026-07-25: 21 rows graded
                    # against NaN closes). A grade that isn't a number is not a grade.
                    if not isfinite(r["ret"]):
                        nonfinite = True
                        continue
                    updates.append(f"{col}=%s"); vals.append(round(r["ret"], 4))
        # benchmark for the 4w window (the primary checkpoint)
        b = t1_return(cur, BENCHMARK, as_of, 28)
        if b is not None and isfinite(b["ret"]):
            updates.append("benchmark_return_4w=%s"); vals.append(round(b["ret"], 4))
        elif b is not None:
            nonfinite = True
        if nonfinite:
            # Leave the row UNGRADED so a later run retries it once the series is repaired —
            # the same "retry later" contract `_has_data_through` already provides.
            pending += 1
            print(f"  ⚠ {ticker} @ {as_of}: non-finite return from the price series — left ungraded")
            continue
        if updates:
            updates += ["entry_date=%s", "entry_px=%s", "fill_convention=%s",
                        "outcome_filled_at=%s"]
            vals += [e_date, e_px, FILL_CONVENTION, today]
            cur.execute(f"UPDATE shift_library SET {', '.join(updates)} WHERE id=%s", vals + [rid])
            graded += 1

    conn.commit()
    conn.close()
    print(f"Graded {graded} rows (4w horizon matured); {pending} pending_data (price series not caught up).")


if __name__ == "__main__":
    main()
