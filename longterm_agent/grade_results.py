"""
Grade matured longterm_agent_results rows: fill fwd_return_4w + benchmark_return_4w once the
4-week horizon has elapsed. This is what gives the Telegram bot a trade win-rate. Mirrors
grade_shifts.py (which grades the shift_library); both read stock_prices_daily and use SPY
as the benchmark. Decisions used data ≤ as_of; only the *outcome* is measured later.

Phase 5.5 additions, run on the same daily cadence:
  - the STOPPED book (structural stop-loss exit, exit_rules.py) is graded alongside the
    hold-to-horizon columns for every matured trade — live data validates the rule forward;
  - open traded rows whose current position return has breached the stop trigger a
    one-time Telegram alert (exit_alert_at marks sent).

    python -m longterm_agent.grade_results
"""
import argparse
from datetime import datetime, timedelta, date

import psycopg2

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.grade_shifts import _ret, _has_data_through  # shared grading helpers
from longterm_agent.exit_rules import fetch_price_path, simulate_exit, stopped_returns

BENCHMARK = "SPY"
HORIZON_DAYS = 28  # 4 weeks (the primary checkpoint)


def _ensure_exit_columns(cur):
    cur.execute("""ALTER TABLE longterm_agent_results
                   ADD COLUMN IF NOT EXISTS fwd_return_stopped REAL,
                   ADD COLUMN IF NOT EXISTS benchmark_return_stopped REAL,
                   ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(12),
                   ADD COLUMN IF NOT EXISTS exit_days INTEGER,
                   ADD COLUMN IF NOT EXISTS exit_alert_at DATE""")


def scan_open_breaches(cur, cfg, today) -> int:
    """One-time Telegram alert for open trades whose position return breached the stop.
    Best-effort: alerting must never block grading."""
    cur.execute("""
        SELECT id, ticker, as_of, verdict FROM longterm_agent_results
        WHERE agent_version=%s AND traded AND verdict IN ('LONG','SHORT')
          AND outcome_filled_at IS NULL AND exit_alert_at IS NULL
    """, [cfg.AGENT_VERSION])
    open_rows = cur.fetchall()
    if not open_rows:
        return 0
    try:
        from core.notify import TelegramNotifier
        notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    except Exception:
        notifier = None
    alerted = 0
    for rid, ticker, as_of, verdict in open_rows:
        days_open = min((today - as_of).days, HORIZON_DAYS)
        if days_open <= 0:
            continue
        path = [(d, c) for d, c in fetch_price_path(cur, ticker, as_of, days_open) if d <= today]
        sim = simulate_exit(path, as_of, verdict, days_open,
                            stop_loss=cfg.STOP_LOSS_PCT, take_profit=cfg.TAKE_PROFIT_PCT)
        if not sim or sim["reason"] != "stop":
            continue
        sign = 1 if verdict == "LONG" else -1
        pos = sign * (sim["exit_px"] - sim["entry_px"]) / sim["entry_px"]
        msg = (f"⛔ STOP BREACH {ticker} {verdict} (as_of {as_of}): position {pos:+.1%} "
               f"through the {cfg.STOP_LOSS_PCT:.0%} stop on {sim['exit_date']} — "
               f"exit per Phase 5.5 discipline")
        print(f"  {msg}")
        if notifier and notifier.enabled():
            try:
                notifier.send(msg)
            except Exception as e:
                print(f"  ⚠ stop-breach alert failed: {e}")
        cur.execute("UPDATE longterm_agent_results SET exit_alert_at=%s WHERE id=%s",
                    [today, rid])
        alerted += 1
    return alerted


def main():
    ap = argparse.ArgumentParser(description="Grade matured longterm_agent_results rows (4w vs SPY)")
    ap.add_argument("--asof-today", default=None, help="override 'today' (YYYY-MM-DD) for testing")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    today = datetime.strptime(args.asof_today, "%Y-%m-%d").date() if args.asof_today else date.today()

    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    _ensure_exit_columns(cur)
    cur.execute("""
        SELECT id, ticker, as_of, verdict, outcome_filled_at FROM longterm_agent_results
        WHERE agent_version=%s AND verdict IN ('LONG','SHORT')
          AND (outcome_filled_at IS NULL OR (traded AND exit_reason IS NULL))
        ORDER BY as_of
    """, [cfg.AGENT_VERSION])
    rows = cur.fetchall()
    print(f"{len(rows)} rows to grade/backfill; today={today}")

    graded, pending, backfilled = 0, 0, 0
    max_dates = {}
    for rid, ticker, as_of, verdict, already in rows:
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
        # stopped book (Phase 5.5) — same trade under the structural exit rule
        stopped = stopped_returns(cur, ticker, as_of, verdict, HORIZON_DAYS,
                                  benchmark=BENCHMARK, stop_loss=cfg.STOP_LOSS_PCT,
                                  take_profit=cfg.TAKE_PROFIT_PCT)
        if stopped:
            cur.execute("""UPDATE longterm_agent_results
                           SET fwd_return_stopped=%s, benchmark_return_stopped=%s,
                               exit_reason=%s, exit_days=%s WHERE id=%s""",
                        [round(stopped["fwd"], 4), round(stopped["bench"], 4),
                         stopped["reason"], stopped["days"], rid])
        if already is None:
            cur.execute("""
                UPDATE longterm_agent_results
                SET fwd_return_4w=%s, benchmark_return_4w=%s, outcome_filled_at=%s WHERE id=%s
            """, [round(r, 4), round(b, 4), today, rid])
            graded += 1
        else:
            backfilled += 1

    alerted = scan_open_breaches(cur, cfg, today)
    conn.commit()
    conn.close()
    print(f"Graded {graded} rows (4w horizon matured); {pending} pending_data "
          f"(price series not caught up); stopped-book backfilled {backfilled}; "
          f"stop-breach alerts {alerted}.")
    return graded


if __name__ == "__main__":
    main()
