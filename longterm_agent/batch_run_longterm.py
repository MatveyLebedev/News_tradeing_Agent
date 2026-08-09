"""
Weekly batch runner (Phase 6) — runs the FULL pipeline across the ticker universe for one
`as_of` (default today), persists each result, logs to the operational log, and pushes a
Telegram alert for every new or flipped directional signal plus a run summary.

    python -m longterm_agent.batch_run_longterm                       # today, full universe
    python -m longterm_agent.batch_run_longterm --tickers NVDA AAPL   # subset
    python -m longterm_agent.batch_run_longterm --as-of 2026-06-15 --no-alert

Designed to be called by the scheduler (auto_longterm_agent.py) or run by hand. Each ticker
is isolated: a failure is logged + (optionally) alerted, and the batch continues.
"""
import time
import argparse
import traceback
from datetime import datetime

import psycopg2
import pytz

from longterm_agent.exit_rules import SESSION_CLOSE, in_session

ET = pytz.timezone("America/New_York")


from longterm_agent.config import LongTermAgentConfig
from longterm_agent.agent.graph import create_longterm_graph
from longterm_agent.persistence import persist_run
from longterm_agent import oplog
from longterm_agent.notifier import TelegramNotifier
from longterm_agent.llm_cache import maybe_enable_llm_cache
from news_agent.database.db_manager import DBManager


def _previous_verdict(cfg, ticker, as_of) -> str:
    """Most recent verdict for this ticker strictly before `as_of` (for flip detection)."""
    try:
        conn = psycopg2.connect(**cfg.get_db_config())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT verdict FROM longterm_agent_results
                WHERE agent_version=%s AND ticker=%s AND as_of < %s
                ORDER BY as_of DESC LIMIT 1
            """, [cfg.AGENT_VERSION, ticker.upper(), as_of.date()])
            r = cur.fetchone()
        conn.close()
        return r[0] if r else None
    except Exception:
        return None


def _already_done(cfg, as_of) -> set:
    """Tickers already persisted for (DETECTION_VERSION, as_of) — the Phase 8 idempotent
    resume set. A crashed batch (OOM, deploy, docker restart) re-runs and pays only for
    the one half-finished ticker; completed ones are skipped, no duplicate rows. Fails
    open (empty set): persist_run's upsert keeps a re-run harmless anyway."""
    try:
        conn = psycopg2.connect(**cfg.get_db_config())
        with conn.cursor() as cur:
            cur.execute("""SELECT ticker FROM longterm_agent_results
                           WHERE agent_version=%s AND as_of=%s""",
                        [cfg.DETECTION_VERSION, as_of.date()])
            done = {r[0] for r in cur.fetchall()}
        conn.close()
        return done
    except Exception:
        return set()


def pending_tickers(tickers, done: set, force: bool = False):
    """Pure: batch order preserved, completed tickers dropped (unless force)."""
    if force:
        return list(tickers)
    return [t for t in tickers if t.upper() not in done]


def decision_moment(batch_as_of, now=None):
    """The `as_of` ONE ticker decides on, given the batch's cohort `as_of`.

    The batch runs serially for ~1.6h (measured: 06:01→07:33 on 2026-08-02), and until now
    every ticker was handed the batch's start moment. That made the last ticker trade on
    data 1.6h older than its own fill, while the first traded on data 3 minutes old. The PIT
    machinery was always built for an intraday moment — `pit.ingest_cutoff` documents `as_of`
    as "an ET wall-clock decision moment" and only falls back to midnight for a bare date —
    so refreshing it per ticker tightens the cutoff rather than changing what it means.

    The cohort key is unaffected: `longterm_agent_results.as_of` is a DATE column and
    `persist_run` stores `as_of.date()`, so all tickers still land in one cohort.

    Two cases must NOT be refreshed:
      * a historical replay (`--as-of 2026-06-15`) — substituting `now` would silently turn
        a replay into a live run against today's data;
      * a batch that crosses midnight — a later date would split the cohort in two and break
        the resume set, which keys on the date.
    """
    now = now or datetime.now(ET).replace(tzinfo=None)
    if batch_as_of.date() != now.date():
        return batch_as_of
    return now


# Measured pace: 64 tickers in ~96 minutes (2026-08-02, 06:01→07:33), so ~1.5 min each.
# Rounded up, because the cost of stopping one ticker early is one missing name and the cost
# of starting one too late is a cohort with two fill conventions in it.
PER_TICKER_MINUTES = 2.0


def session_guard(batch_as_of, now, per_ticker_minutes: float = PER_TICKER_MINUTES):
    """(may_continue, reason) — may another ticker be started without splitting the cohort?

    `resolve_entry` picks the fill convention from each row's own clock: in-session rows get
    `mid_1m`, everything else gets the daily rule. That is correct per row and ruinous per
    cohort — a batch that starts at 12:00 and overruns 16:00 produces one cohort holding two
    conventions, with no error anywhere, and its `excess` distribution silently mixes two
    fill semantics. Not hypothetical: one live batch ran 06:23→15:39 (2026-07-26); from a
    midday start that finishes near 21:00.

    Stopping leaves an INCOMPLETE cohort, which is the better failure: fewer names is a
    smaller n and stays analysable, while a complete-but-mixed cohort is corrupt data
    wearing the shape of good data.

    Inert for an out-of-session batch (today's Sunday 06:00 book): every row there resolves
    to the same daily convention, so there is nothing to split.
    """
    if not in_session(batch_as_of):
        return True, None
    if not in_session(now):
        return False, f"session closed at {now:%H:%M} ET"
    left_min = (datetime.combine(now.date(), SESSION_CLOSE) - now).total_seconds() / 60.0
    if left_min < per_ticker_minutes:
        return False, f"only {left_min:.0f} min of session left"
    return True, None


def lagged_decisions(rows):
    """[(ticker, lag_days)] for rows decided on a different DAY than their cohort.

    The daily fill convention prices a trade from `as_of` — the first bar strictly after it.
    That is only sound while the decision was made no later than `as_of`. A batch resumed on
    a later day breaks the assumption without touching anything that would notice:
    `_already_done` keys the resume on `(version, as_of)`, so the re-run correctly rejoins
    the same cohort, while `run_timestamp` is stamped NOW() and drifts.

    Measured consequence, cohort 2026-07-26: 59 rows were written on Tuesday the 28th, and
    all 6 traded ones resolved to an entry at MONDAY the 27th's open — a price that existed
    before the decision did. Look-ahead, produced silently, found only because the cohort
    was inspected by hand.

    Pure so the check is unit-tested; the caller decides how loudly to complain.
    """
    out = []
    for ticker, as_of, ts in rows:
        if ts is None or as_of is None:
            continue
        lag = (ts.date() - as_of).days
        if lag != 0:
            out.append((ticker, lag))
    return out


def _as_naive_et(ts):
    """`run_timestamp` is timestamptz; the session boundary is an ET wall clock."""
    if ts is None:
        return None
    return ts.astimezone(ET).replace(tzinfo=None) if ts.tzinfo else ts


def convention_split(run_timestamps) -> bool:
    """Did this cohort's rows straddle the session boundary?

    Checked on `run_timestamp`, deliberately NOT on `fill_convention`: the latter is stamped
    by the grader at maturity, four weeks later, which is useless as an alarm. The decision
    clock is known the moment the row is written.
    """
    flags = {in_session(t) for t in run_timestamps if t is not None}
    return len(flags) > 1


def run_batch(tickers, as_of, cfg, db, neo, llm_map, llm_reduce, notifier, alert=True,
              freshness=None, force=False):
    graph = create_longterm_graph(llm_map, llm_reduce, cfg, db, neo)
    stats = {"runs": 0, "signals": 0, "trades": 0, "no_data": 0, "errors": 0, "skipped": 0,
             "not_persisted": 0}
    done = _already_done(cfg, as_of)
    todo = pending_tickers(tickers, done, force=force)
    stats["skipped"] = len(tickers) - len(todo)
    if stats["skipped"]:
        print(f"  resume: skipping {stats['skipped']} tickers already persisted for "
              f"({cfg.DETECTION_VERSION}, {as_of.date()})")
    oplog.info("BATCH_START", f"weekly batch: {len(todo)} tickers ({stats['skipped']} resumed-skip)",
               as_of=as_of, details={"tickers": todo, "version": cfg.AGENT_VERSION,
                                     "skipped": stats["skipped"]})

    for ticker in todo:
        may, why = session_guard(as_of, datetime.now(ET).replace(tzinfo=None))
        if not may:
            stats["stopped_early"] = len(todo) - stats["runs"]
            msg = (f"⛔ batch stopped at {ticker} — {why}. {stats['stopped_early']} of "
                   f"{len(todo)} tickers not run. The cohort {as_of.date()} is INCOMPLETE "
                   f"but single-convention; finishing it would have split it across "
                   f"mid_1m and open_t1.")
            print(f"  {msg}")
            oplog.error("BATCH_SESSION_STOP", msg, as_of=as_of,
                        details={"remaining": stats["stopped_early"], "reason": why})
            if alert:
                notifier.send(msg)
            break
        stats["runs"] += 1
        prev = _previous_verdict(cfg, ticker, as_of)
        try:
            # Per-ticker, not the batch's start: see decision_moment(). The cohort key is
            # the DATE, so this refreshes the data cutoff without moving the ticker to a
            # different cohort.
            init = {"ticker": ticker.upper(), "as_of": decision_moment(as_of),
                    "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND,
                    "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": [],
                    # Defense-in-depth behind the preflight: risk_gate caps conviction
                    # when any source is stale (only relevant with --skip-freshness).
                    "data_freshness": freshness}
            t0 = time.time()
            final = graph.invoke(init)
            elapsed = time.time() - t0
            # A decision the book never records is invisible everywhere downstream — /open,
            # /week and grading all read the results table, so an unpersisted trade looks
            # exactly like "the agent had no opinion" (2026-07-26 lost a full cohort that
            # way). Surface it with the same weight as a run failure.
            if not persist_run(db, final, cfg, execution_time=elapsed):
                stats["not_persisted"] += 1
                if alert and cfg.ALERT_ON_ERROR:
                    notifier.notify_error(
                        f"{ticker} @ {as_of.date()}",
                        "decision NOT persisted — see PERSIST_FAILED in the op log")

            if final.get("no_data"):
                stats["no_data"] += 1
                oplog.info("NO_DATA", f"{ticker}: {final.get('article_count', 0)} articles",
                           ticker=ticker, as_of=as_of)
                continue

            sig = final.get("signal") or {}
            direction = sig.get("direction") or "NEUTRAL"
            traded = final.get("filtered_signal") is not None and direction in ("LONG", "SHORT")
            if direction in ("LONG", "SHORT"):
                stats["signals"] += 1
            if traded:
                stats["trades"] += 1

            is_new = direction in ("LONG", "SHORT") and prev not in (direction,)
            oplog.info("TRADE" if traded else "RUN_OK",
                       f"{ticker}: {direction} (conv {sig.get('conviction')}, prev {prev})",
                       ticker=ticker, as_of=as_of,
                       details={"direction": direction, "traded": traded,
                                "conviction": sig.get("conviction"), "elapsed": round(elapsed, 1)})

            # Alert on a new directional signal or a direction flip.
            if alert and is_new:
                notifier.notify_signal(
                    ticker, as_of.date(), direction, sig.get("conviction"),
                    sig.get("horizon"), sig.get("thesis"), traded, previous=prev)

        except Exception as e:
            stats["errors"] += 1
            detail = f"{type(e).__name__}: {e}"
            oplog.error("RUN_ERROR", f"{ticker}: {detail}", ticker=ticker, as_of=as_of,
                        details={"traceback": traceback.format_exc()[-2000:]})
            print(f"  ❌ {ticker}: {detail}")
            if alert and cfg.ALERT_ON_ERROR:
                notifier.notify_error(f"{ticker} @ {as_of.date()}", detail)

    # Second line of defence: the preventive guard cannot see a hand-run batch or a single
    # ticker that hung past the close, so the cohort is inspected after the fact too.
    try:
        conn = psycopg2.connect(**cfg.get_db_config())
        with conn.cursor() as c:
            c.execute("""SELECT ticker, as_of, run_timestamp, traded
                           FROM longterm_agent_results
                          WHERE agent_version=%s AND as_of=%s""",
                      [cfg.DETECTION_VERSION, as_of.date()])
            fetched = [(tk, a, _as_naive_et(t), tr) for tk, a, t, tr in c.fetchall()]
        conn.close()
        stamps = [t for _, _, t, _ in fetched]

        lagged = lagged_decisions([(tk, a, t) for tk, a, t, _ in fetched])
        if lagged:
            traded_lagged = [tk for tk, a, t, tr in fetched
                             if tr and any(tk == x for x, _ in lagged)]
            msg = (f"🚨 cohort {as_of.date()}: {len(lagged)} row(s) DECIDED ON A DIFFERENT "
                   f"DAY than the cohort (max lag {max(l for _, l in lagged)}d). The daily "
                   f"fill prices them from as_of, i.e. BEFORE the decision existed — "
                   f"look-ahead. Traded and therefore leaking: "
                   f"{', '.join(traded_lagged) or 'none'}. Mark them with pit_excluded.")
            print(f"  {msg}")
            oplog.error("COHORT_DECISION_LAG", msg, as_of=as_of,
                        details={"lagged": lagged, "traded": traded_lagged})
            if alert:
                notifier.send(msg)

        if convention_split(stamps):
            msg = (f"⚠️ cohort {as_of.date()} SPLIT ACROSS THE SESSION BOUNDARY: its rows "
                   f"will grade under two fill conventions (mid_1m and open_t1). Not "
                   f"comparable to the ledger as one book.")
            print(f"  {msg}")
            oplog.error("COHORT_CONVENTION_SPLIT", msg, as_of=as_of)
            if alert:
                notifier.send(msg)
    except Exception as e:
        print(f"  ⚠ convention-split check failed: {e}")

    oplog.info("BATCH_DONE", f"batch complete: {stats}", as_of=as_of, details=stats)
    if alert:
        notifier.notify_batch_summary(as_of.date(), stats)
    return stats


def main():
    ap = argparse.ArgumentParser(description="Weekly full-pipeline batch run for the long-term agent")
    ap.add_argument("--tickers", nargs="+", help="default: full universe (LongTermAgentConfig.get_universe)")
    ap.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--lookback", type=int, default=None)
    ap.add_argument("--no-alert", action="store_true", help="do not send Telegram alerts")
    ap.add_argument("--trace", action="store_true", help="enable LangSmith tracing")
    ap.add_argument("--skip-freshness", action="store_true",
                    help="bypass the stale-data preflight (deliberate replays only)")
    ap.add_argument("--force", action="store_true",
                    help="re-run tickers already persisted for this (version, as_of)")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    if args.trace:
        cfg.enable_tracing()
    maybe_enable_llm_cache()  # LT_LLM_CACHE=true → reproducible & cheap re-runs (B0)
    if args.lookback:
        cfg.LOOKBACK_DAYS_BACKGROUND = args.lookback
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d")
    tickers = args.tickers or cfg.get_universe()

    # FRESHNESS PREFLIGHT (Phase 2): a live batch on frozen sources produces results
    # that look normal and are silently wrong (the 2026-06-15 SPY freeze) — refuse to
    # start instead. Enforced only for live as_of (historical as_of = deliberate replay).
    # Prices get a tight threshold (2 weekdays): the batch runs the morning after the
    # Saturday fetch, so "fresh" here means "the Saturday fetch actually happened".
    freshness = None
    if (datetime.now() - as_of).days <= 7 and not args.skip_freshness:
        from core.freshness import check_freshness, format_report
        freshness = check_freshness(db_config=cfg.get_db_config(),
                                    thresholds={"prices": 2, "prices_spy": 2})
        if freshness["any_stale"]:
            msg = f"stale sources {freshness['stale']} — batch ABORTED\n{format_report(freshness)}"
            print(f"❌ preflight: {msg}")
            oplog.error("PREFLIGHT_STALE", msg, as_of=as_of, details=freshness)
            if not args.no_alert:
                TelegramNotifier().notify_error("batch preflight", msg)
            return

    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=5)
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e}); corpus uses regex-only")

    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    llm_reduce = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=8000)
    notifier = TelegramNotifier()

    print(f"Batch: {len(tickers)} tickers @ as_of {args.as_of} "
          f"(alerts {'OFF' if args.no_alert else 'ON' if notifier.enabled else 'unconfigured'})")
    t0 = time.time()
    stats = run_batch(tickers, as_of, cfg, db, neo, llm_map, llm_reduce,
                      notifier, alert=not args.no_alert, freshness=freshness,
                      force=args.force)
    print(f"\nBatch complete in {time.time()-t0:.0f}s: {stats}")
    if neo:
        neo.close()


if __name__ == "__main__":
    main()
