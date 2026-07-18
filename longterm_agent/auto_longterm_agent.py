"""
Scheduler for the long-term agent (Phase 6 ops) — the long-running container entrypoint.

Two scheduled jobs (ET), same `schedule`-library pattern as auto_news_fetcher.py:
  * WEEKLY batch  (LT_BATCH_WEEKDAY @ LT_BATCH_TIME): run the full pipeline across the
    universe for today's as_of, persist, alert new/flipped signals, then grade matured cohorts.
  * DAILY grading (LT_GRADE_TIME): fill realized 4w outcomes on matured trades + shift rows.

Run the weekly batch *after* the news/fundamentals fetchers have updated the DB for the week.

    python -m longterm_agent.auto_longterm_agent           # start the scheduler
    python -m longterm_agent.auto_longterm_agent --now     # run one batch immediately, then schedule
    python -m longterm_agent.auto_longterm_agent --grade-now
"""
import time
import argparse
import traceback
from datetime import datetime

import pytz
import schedule


from longterm_agent.config import LongTermAgentConfig
from longterm_agent import oplog
from longterm_agent.notifier import TelegramNotifier
from longterm_agent.batch_run_longterm import run_batch
from news_agent.database.db_manager import DBManager

ET = pytz.timezone("America/New_York")


class Ops:
    """Holds the shared, reusable objects (DB, Neo4j, LLM clients, notifier)."""

    def __init__(self):
        self.cfg = LongTermAgentConfig()
        if self.cfg.TRACE:
            # Before make_llm so callbacks attach to every call (see run_longterm_agent).
            self.cfg.enable_tracing()
        self.notifier = TelegramNotifier()
        self.db = DBManager(db_config=self.cfg.get_db_config(), min_conn=1, max_conn=5)
        self.neo = None
        try:
            from graph_db.neo4j_manager import Neo4jManager
            self.neo = Neo4jManager()
        except Exception as e:
            print(f"⚠ Neo4j unavailable ({e}); corpus uses regex-only")
        self.llm_map = self.cfg.make_llm(model=self.cfg.MAP_MODEL, max_tokens=2048)
        self.llm_reduce = self.cfg.make_llm(model=self.cfg.LLM_MODEL, max_tokens=8000)

    # --- jobs --------------------------------------------------------------
    def weekly_batch(self):
        as_of = datetime.now(ET).replace(tzinfo=None)
        tickers = self.cfg.get_universe()
        print(f"\n{'='*80}\nWEEKLY BATCH @ {as_of:%Y-%m-%d %H:%M} ET · {len(tickers)} tickers\n{'='*80}")
        try:
            run_batch(tickers, as_of, self.cfg, self.db, self.neo,
                      self.llm_map, self.llm_reduce, self.notifier, alert=True)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("BATCH_FATAL", detail, as_of=as_of,
                        details={"traceback": traceback.format_exc()[-2000:]})
            self.notifier.notify_error("weekly_batch", detail)
            print(f"❌ weekly batch failed: {detail}")
        # Grade whatever matured this week right after producing new signals.
        self.daily_grade()
        # Weekly trades digest (new / open / closed) — same text as the bot's /week.
        try:
            from longterm_agent.stats import weekly_report, format_weekly_report
            self.notifier.send("📆 " + format_weekly_report(weekly_report()))
        except Exception as e:
            print(f"  ⚠ weekly digest failed: {e}")

    def daily_grade(self):
        print(f"\n--- GRADING @ {datetime.now(ET):%Y-%m-%d %H:%M} ET ---")
        try:
            from longterm_agent.grade_results import main as grade_results_main
            from longterm_agent.grade_shifts import main as grade_shifts_main
            n_res = grade_results_main() or 0
            grade_shifts_main()
            oplog.info("GRADE", f"graded {n_res} matured result rows")
        except SystemExit:
            pass  # argparse in the grading mains may call sys.exit on --help only
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("GRADE_ERROR", detail, details={"traceback": traceback.format_exc()[-2000:]})
            self.notifier.notify_error("daily_grade", detail)
            print(f"❌ grading failed: {detail}")


def run_scheduler(run_on_start=False):
    ops = Ops()
    cfg = ops.cfg

    # Weekly full batch
    try:
        day_job = getattr(schedule.every(), cfg.BATCH_WEEKDAY)
        day_job.at(cfg.BATCH_TIME).do(ops.weekly_batch)
    except Exception as e:
        print(f"⚠ could not schedule weekly batch: {e}")
    # Daily grading of matured cohorts
    schedule.every().day.at(cfg.GRADE_TIME).do(ops.daily_grade)

    msg = (f"long-term agent scheduler up · batch {cfg.BATCH_WEEKDAY} {cfg.BATCH_TIME} ET, "
           f"grade daily {cfg.GRADE_TIME} ET · universe {len(cfg.get_universe())}")
    print("=" * 80)
    print(f"LONG-TERM AGENT SCHEDULER — STARTED ({datetime.now(ET):%Y-%m-%d %H:%M %Z})")
    print(msg)
    print("=" * 80)
    oplog.info("SCHEDULER_UP", msg)
    ops.notifier.send(f"🟢 <b>Long-term agent scheduler started</b>\n{msg}")

    nxt = schedule.next_run()
    if nxt:
        print(f"⏰ next job: {nxt:%Y-%m-%d %H:%M}")

    if run_on_start or cfg.RUN_ON_START:
        print("▶ RUN_ON_START — running one batch now…")
        ops.weekly_batch()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nscheduler stopped.")
        oplog.info("SCHEDULER_DOWN", "stopped via KeyboardInterrupt")
        if ops.neo:
            ops.neo.close()


def main():
    ap = argparse.ArgumentParser(description="Long-term agent weekly scheduler")
    ap.add_argument("--now", action="store_true", help="run one weekly batch immediately, then schedule")
    ap.add_argument("--grade-now", action="store_true", help="run grading once and exit")
    args = ap.parse_args()

    if args.grade_now:
        Ops().daily_grade()
        return
    run_scheduler(run_on_start=args.now)


if __name__ == "__main__":
    main()
