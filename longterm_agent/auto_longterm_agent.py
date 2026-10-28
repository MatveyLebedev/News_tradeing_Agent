"""
Scheduler for the long-term agent (Phase 6 ops) — the long-running container entrypoint.

Two scheduled jobs (ET), same `schedule`-library pattern as auto_news_fetcher.py:
  * WEEKLY batch  (LT_BATCH_WEEKDAY @ LT_BATCH_TIME): run the full pipeline across the
    universe for today's as_of, persist, alert new/flipped signals, then grade matured cohorts.
  * DAILY grading (LT_GRADE_TIME): fill realized 4w outcomes on matured trades + shift rows,
    then restate them as trace labels and mirror those into Langfuse (label_traces.py).

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
from contextlib import contextmanager
from datetime import datetime

import pytz
import schedule


from core.db import get_conn
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
    env.update({"LLM_PROVIDER": "novita", "LT_LLM_CACHE": "true", "LT_MAX_REFLECTION": "0",
                # Traced like the live batch (still a no-op without LANGFUSE_* / LT_TRACE=false).
                "LT_TRACE_BACKTEST": "true", "LANGFUSE_TRACING_ENVIRONMENT": "live"})
    return env


class Ops:
    """Holds the shared, reusable objects (DB, Neo4j, LLM clients, notifier)."""

    def __init__(self):
        self.cfg = LongTermAgentConfig()
        if self.cfg.TRACE:
            self.cfg.enable_tracing("live")
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

    @contextmanager
    def _cursor(self):
        """One fresh, committed cursor per job step.

        Deliberately NOT `self.db`'s pool: this process idles for days between jobs, so a
        pooled connection is dead long before the next job runs (the reason core.db adds
        keepalives + retry). Worse, psycopg2's pool rolls back anything handed back mid
        transaction, so pooled broker writes would vanish without ever raising.
        """
        with get_conn(self.cfg.get_db_config()) as conn:
            with conn.cursor() as cur:
                yield cur
            conn.commit()

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
        # Paper broker: open the cohort just written. After grading so a matured position
        # is not opened and closed in the same pass.
        self.broker_entries(as_of)
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
            with self._cursor() as cur:
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

    # --- paper broker ------------------------------------------------------
    def _broker(self):
        """(client, cursor-context) or None when the layer is off/misconfigured.

        Every broker step is wrapped so it can only ever be a no-op: this runs inside the
        job that produces the research book, and a broker outage must not be able to stop a
        cohort from being written or graded.
        """
        if not self.cfg.BROKER_ENABLED:
            return None
        try:
            from longterm_agent.broker.alpaca_client import AlpacaPaper
            return AlpacaPaper()
        except Exception as e:
            print(f"  ⚠ broker unavailable: {type(e).__name__}: {e}")
            return None

    def broker_entries(self, as_of):
        """Entry orders for the cohort just written, then one netted hedge rebalance."""
        client = self._broker()
        if client is None:
            return
        dry = self.cfg.BROKER_DRY_RUN
        tag = "DRY RUN" if dry else "LIVE"
        try:
            from longterm_agent.broker import execution as ex, hedge, ledger
            with self._cursor() as cur:
                ledger.ensure_schema(cur)
                rows = ledger.entry_candidates(cur, self.cfg.DETECTION_VERSION, as_of.date(),
                                               ex.NOTIONAL_PER_POSITION,
                                               self.cfg.BROKER_START_DATE or None)
                if not rows:
                    print(f"  broker [{tag}]: nothing to open for {as_of.date()}")
                    return
                out = ex.submit_entries(client, cur, rows,
                                        agent_version=self.cfg.DETECTION_VERSION,
                                        notifier=self.notifier, dry_run=dry)
                # Hedge the book the BROKER holds, not the research book — until the cutoff
                # ages out the two differ by construction.
                book = rows if dry else ledger.broker_book(
                    cur, self.cfg.DETECTION_VERSION, ex.NOTIONAL_PER_POSITION)
                cur.execute("""SELECT close FROM stock_prices_daily
                                WHERE ticker='SPY' ORDER BY date DESC LIMIT 1""")
                spy = cur.fetchone()
                h = hedge.submit_rebalance(client, cur, book, float(spy[0]),
                                           agent_version=self.cfg.DETECTION_VERSION,
                                           as_of=as_of.date(), dry_run=dry) if spy else {}
            msg = (f"🧾 broker [{tag}] {as_of.date()}: {out['submitted']} entries"
                   + (f", skipped {len(out['skipped'])}" if out["skipped"] else "")
                   + (f", hedge {h.get('target')} SPY" if h else "")
                   + (" · ⚠️ HALTED" if out.get("halted") else ""))
            print("  " + msg)
            oplog.info("BROKER", msg, as_of=as_of, details={"dry_run": dry, **out})
            self.notifier.send(msg)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("BROKER_FAILED", detail, as_of=as_of,
                        details={"traceback": traceback.format_exc()[-2000:]})
            self.notifier.notify_error("broker_entries", detail)

    def broker_exits(self):
        """Close what reached its horizon (MOC) or breached its stop (market).

        Runs from the daily job, which fires at LT_GRADE_TIME — well inside the MOC window,
        so a horizon exit reaches the same closing print the grader scores. Deliberately
        AFTER grading: `scan_open_breaches` stamps `exit_alert_at` in that pass, and this
        reads it.
        """
        client = self._broker()
        if client is None:
            return
        dry = self.cfg.BROKER_DRY_RUN
        tag = "DRY RUN" if dry else "LIVE"
        try:
            from longterm_agent.broker import exits, ledger
            now = datetime.now(ET)
            with self._cursor() as cur:
                # Same self-ensuring schema as entries and reconcile: this job can be the
                # first broker step to touch the DB (entries runs weekly, and reconcile
                # ahead of it may have bailed), and `paper_orders` would not exist yet.
                ledger.ensure_schema(cur)
                rows = exits.closable(cur, self.cfg.DETECTION_VERSION, now.date())
                if not rows:
                    return
                out = exits.submit_exits(client, cur, rows,
                                         agent_version=self.cfg.DETECTION_VERSION,
                                         now_time=now.time(), dry_run=dry)
            msg = (f"🔚 broker [{tag}]: {out['exits']} horizon exits, {out['stops']} stops"
                   + (f", deferred past the MOC window: {', '.join(out['deferred'])}"
                      if out["deferred"] else ""))
            print("  " + msg)
            oplog.info("BROKER_EXIT", msg, details={"dry_run": dry, **out})
            self.notifier.send(msg)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("BROKER_EXIT_FAILED", detail,
                        details={"traceback": traceback.format_exc()[-2000:]})
            self.notifier.notify_error("broker_exits", detail)

    def broker_reconcile(self):
        """Pull fills, then compare the two books. Runs daily, alongside grading."""
        client = self._broker()
        if client is None:
            return
        try:
            from longterm_agent.broker import reconcile
            with self._cursor() as cur:
                out = reconcile.run(cur, notifier=self.notifier,
                                    agent_version=self.cfg.DETECTION_VERSION,
                                    client=client)
            print(f"  broker reconcile: {out['sync']}")
            oplog.info("BROKER_SYNC", f"reconcile: {out['sync']}", details=out["sync"])
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("BROKER_SYNC_FAILED", detail,
                        details={"traceback": traceback.format_exc()[-2000:]})
            self.notifier.notify_error("broker_reconcile", detail)

    def daily_grade(self):
        print(f"\n--- GRADING @ {datetime.now(ET):%Y-%m-%d %H:%M} ET ---")
        try:
            from longterm_agent.grade_results import main as grade_results_main
            from longterm_agent.grade_shifts import main as grade_shifts_main
            n_res = grade_results_main() or 0
            grade_shifts_main()
            oplog.info("GRADE", f"graded {n_res} matured result rows")
            # Sync fills first so an entry that just filled is closable, then close what is
            # due — grading has already stamped any stop breach by this point.
            self.broker_reconcile()
            self.broker_exits()
        except SystemExit:
            pass  # argparse in the grading mains may call sys.exit on --help only
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("GRADE_ERROR", detail, details={"traceback": traceback.format_exc()[-2000:]})
            self.notifier.notify_error("daily_grade", detail)
            print(f"❌ grading failed: {detail}")
        self.label_traces()

    def label_traces(self):
        """Restate today's grades as trace labels and mirror them into Langfuse. Runs after
        every grading step and is best-effort: a labeling failure never holds up a grade, a
        fill or an exit, and is logged rather than paged (Langfuse being down is not news)."""
        try:
            from longterm_agent.label_traces import main as label_main
            stats = label_main([])
            oplog.info("LABEL", f"trace labels: {stats}")
        except SystemExit:
            pass
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            oplog.error("LABEL_ERROR", detail,
                        details={"traceback": traceback.format_exc()[-2000:]})
            print(f"⚠ trace labeling failed: {detail}")


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
