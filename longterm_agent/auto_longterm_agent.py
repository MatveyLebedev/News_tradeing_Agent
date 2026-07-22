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
import os
import subprocess
import sys
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

# The 30 tickers frozen with the `monday_t1_fwd` registration (docs/BACKTEST_LEDGER.md).
# Deliberately NOT cfg.get_universe(): the forward book must stay comparable to
# monday_t1_pit cohort over cohort, so its universe is pinned even as the live one grows.
MONDAY_FWD_TICKERS = [
    "AAPL", "ADBE", "AMD", "AMZN", "AVGO", "BAC", "C", "CRM", "DIS", "GOOGL",
    "GS", "INTC", "JPM", "KO", "LLY", "MA", "META", "MS", "MSFT", "NFLX",
    "NVDA", "ORCL", "PEP", "PFE", "QCOM", "TSLA", "UBER", "V", "WMT", "XOM",
]


def monday_fwd_command(run_date, tickers=MONDAY_FWD_TICKERS):
    """argv for one forward Monday cohort. Pure so the invocation is unit-tested —
    a wrong flag here would silently produce a book under a different convention."""
    return [sys.executable, "-m", "longterm_agent.backtest",
            "--backtest-id", "monday_t1_fwd",
            "--tickers", *tickers,
            "--start", str(run_date), "--end", str(run_date), "--step", "7",
            "--horizons", "14", "28", "42", "56", "84",
            "--workers", "4", "--benchmark", "SPY", "--fill", "d0", "--at", "06:30"]


def monday_fwd_env(base=None):
    """Env for the cohort subprocess. A SUBPROCESS on purpose: the provider pin must not
    leak into the live batch process — pinning novita globally 404s every gemini map call
    (measured 2026-07-20, aborted a run)."""
    env = dict(base if base is not None else os.environ)
    env.update({"LLM_PROVIDER": "novita", "LT_LLM_CACHE": "true", "LT_MAX_REFLECTION": "0"})
    return env


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

    def monday_forward_cohort(self):
        """One `monday_t1_fwd` cohort, run ON its Monday (docs/BACKTEST_LEDGER.md).

        Scheduled at 06:35 ET so the as_of stamp (06:30) is minutes old: the measured
        replay-drift mechanism (2026-07-22) means every day between as_of and the run
        adds drift surface, and the no-re-roll protocol makes this first run the cohort.
        """
        today = datetime.now(ET).date()
        print(f"\n--- MONDAY FORWARD COHORT @ {today} ---")
        try:
            r = subprocess.run(monday_fwd_command(today), env=monday_fwd_env(),
                               timeout=3 * 3600)
            ok = r.returncode == 0
        except Exception as e:
            ok = False
            self.notifier.notify_error("monday_fwd", f"{type(e).__name__}: {e}")
        # Health check, not silence: a failed synthesis falls back to NEUTRAL, so an
        # elevated rate deflates the cohort invisibly. >5% ⇒ patch the failed tickers
        # by hand (never re-roll) — the alert is what makes that happen.
        try:
            from longterm_agent import backtest_metrics as M
            with self.db.get_cursor() as cur:
                cur.execute("""SELECT signal FROM backtest_runs
                               WHERE backtest_id='monday_t1_fwd' AND horizon_days=28
                                 AND as_of::date=%s""", [today])
                sigs = [row[0] for row in cur.fetchall()]
            rate = M.failure_rate(sigs)
            flag = "⚠️ ABOVE 5% — patch failed tickers individually" if rate > 0.05 else "ok"
            self.notifier.send(
                f"📅 monday_t1_fwd cohort {today}: rows {len(sigs)}, "
                f"failed synth {rate:.1%} ({flag}); runner "
                f"{'exit 0' if ok else 'FAILED'}")
            oplog.info("MONDAY_FWD", f"cohort {today}: n={len(sigs)} fail={rate:.1%} ok={ok}")
        except Exception as e:
            print(f"  ⚠ monday_fwd health check failed: {e}")

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
    # Forward Monday cohort (LT_MONDAY_FWD=false to disable)
    if cfg.MONDAY_FWD:
        schedule.every().monday.at("06:35").do(ops.monday_forward_cohort)

    msg = (f"long-term agent scheduler up · batch {cfg.BATCH_WEEKDAY} {cfg.BATCH_TIME} ET, "
           f"grade daily {cfg.GRADE_TIME} ET · universe {len(cfg.get_universe())}"
           + (" · monday_fwd 06:35" if cfg.MONDAY_FWD else ""))
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
