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


def run_batch(tickers, as_of, cfg, db, neo, llm_map, llm_reduce, notifier, alert=True, freshness=None):
    graph = create_longterm_graph(llm_map, llm_reduce, cfg, db, neo)
    stats = {"runs": 0, "signals": 0, "trades": 0, "no_data": 0, "errors": 0}
    oplog.info("BATCH_START", f"weekly batch: {len(tickers)} tickers",
               as_of=as_of, details={"tickers": tickers, "version": cfg.AGENT_VERSION})

    for ticker in tickers:
        stats["runs"] += 1
        prev = _previous_verdict(cfg, ticker, as_of)
        try:
            init = {"ticker": ticker.upper(), "as_of": as_of,
                    "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND,
                    "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": [],
                    # Defense-in-depth behind the preflight: risk_gate caps conviction
                    # when any source is stale (only relevant with --skip-freshness).
                    "data_freshness": freshness}
            t0 = time.time()
            final = graph.invoke(init)
            elapsed = time.time() - t0
            persist_run(db, final, cfg, execution_time=elapsed)

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
                      notifier, alert=not args.no_alert, freshness=freshness)
    print(f"\nBatch complete in {time.time()-t0:.0f}s: {stats}")
    if neo:
        neo.close()


if __name__ == "__main__":
    main()
