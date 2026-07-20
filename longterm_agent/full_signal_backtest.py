"""
Full-signal backtest (estimate, not a unit test).

Runs the ENTIRE pipeline per (ticker, as_of) — corpus → profiling → shift → fundamentals
→ analogs → synthesis → reflection → risk_gate — captures the actual LongTermSignal, then
grades it against the realized forward excess return vs SPY.

Key metric = mean STRATEGY excess: for a LONG you earn +excess, for a SHORT you earn −excess.
Only signals that PASS the risk gate count as trades.

Fill convention `open_t1` (exit_rules), same as every other book: entry = OPEN of the first
bar strictly after as_of, horizon anchored at that entry. This script used to grade with the
legacy close-to-close helper, which entered at the last close ≤ as_of — a price printed
before news the decision had already read, with the untradable gap credited to the trade.

Leans on the warm bucket cache (re-use the detection-replay tickers/cohorts) and runs with
reflection off (set LT_MAX_REFLECTION=0) to keep cost down.

    LT_MAX_REFLECTION=0 python -m longterm_agent.full_signal_backtest \
        --tickers NVDA AAPL MSFT TSLA AMD JPM AMZN META --start 2026-04-30 --end 2026-05-18
"""
import argparse
import statistics
from collections import Counter
from datetime import datetime, timedelta

import psycopg2

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.agent.graph import create_longterm_graph
from longterm_agent.exit_rules import FILL_CONVENTION, t1_return
from news_agent.database.db_manager import DBManager


def _weekly(start, end, step):
    d, out = start, []
    while d <= end:
        out.append(d)
        d += timedelta(days=step)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--horizon", type=int, default=28)
    ap.add_argument("--trace", action="store_true", help="enable LangSmith tracing")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    if args.trace:
        cfg.enable_tracing()
    from longterm_agent.llm_cache import maybe_enable_llm_cache
    maybe_enable_llm_cache()  # LT_LLM_CACHE=true → reproducible backtest (B0)
    print(f"provenance: {cfg.provenance()}")
    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=5)
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e})")

    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    llm_reduce = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=8000)
    graph = create_longterm_graph(llm_map, llm_reduce, cfg, db, neo)

    cohorts = _weekly(datetime.strptime(args.start, "%Y-%m-%d"), datetime.strptime(args.end, "%Y-%m-%d"), args.step)

    print(f"Full-signal backtest: {len(args.tickers)} tickers × {len(cohorts)} cohorts "
          f"(reflection iters={cfg.MAX_REFLECTION_ITERATIONS})")
    # Phase 1: RUN the pipeline, collect signals (no DB grading inline — a held cursor would
    # go stale across the long LLM gaps; grade afterwards with a fresh connection).
    results = []
    for as_of in cohorts:
        for tk in args.tickers:
            try:
                final = graph.invoke({"ticker": tk.upper(), "as_of": as_of,
                                      "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND,
                                      "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": []})
            except Exception as e:
                print(f"  {as_of.date()} {tk}: ERROR {e}")
                continue
            if final.get("no_data"):
                results.append({"tk": tk, "as_of": as_of, "verdict": "NO_DATA"})
                continue
            sig = final.get("signal", {})
            results.append({"tk": tk, "as_of": as_of, "dir": sig.get("direction"),
                            "conv": sig.get("conviction"),
                            "passed": final.get("filtered_signal") is not None, "excess": None})
            print(f"  ran {as_of.date()} {tk}: {sig.get('direction')} conv={sig.get('conviction')} "
                  f"passed={results[-1]['passed']}")
    if neo:
        neo.close()

    # Phase 2: GRADE all collected signals with a single fresh connection (no LLM gaps now).
    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    for r in results:
        if r.get("dir") is None:
            continue
        fwd = t1_return(cur, r["tk"], r["as_of"].date(), args.horizon)
        spy = t1_return(cur, "SPY", r["as_of"].date(), args.horizon)
        r["excess"] = (fwd["ret"] - spy["ret"]) if (fwd and spy) else None
    conn.close()

    # ---- report ----
    def signed(r):
        return r["excess"] if r["dir"] == "LONG" else -r["excess"]

    allsig = [r for r in results if r.get("dir")]
    traded = [r for r in results if r.get("passed") and r.get("dir") in ("LONG", "SHORT") and r.get("excess") is not None]

    print("\n" + "=" * 70)
    print(f"FULL-SIGNAL BACKTEST RESULT  (fill convention: {FILL_CONVENTION})")
    print("=" * 70)
    print(f"runs={len(results)}  no_data={sum(1 for r in results if r.get('verdict') == 'NO_DATA')}")
    print(f"signals={len(allsig)}  direction mix={dict(Counter(r['dir'] for r in allsig))}")
    print(f"traded (passed risk gate)={len(traded)}")
    if traded:
        se = [signed(r) for r in traded]
        wins = sum(1 for x in se if x > 0)
        print(f"\nTRADED signals (n={len(traded)}):")
        print(f"  win rate: {wins/len(traded):.0%}")
        print(f"  mean strategy excess 4w: {statistics.mean(se):+.2%}   median {statistics.median(se):+.2%}")
        print(f"  mean conviction: {statistics.mean([r['conv'] for r in traded]):.2f}")
        for d in ("LONG", "SHORT"):
            sub = [r for r in traded if r["dir"] == d]
            if sub:
                s = [signed(r) for r in sub]
                print(f"  {d}: n={len(sub)} win={sum(1 for x in s if x > 0)/len(sub):.0%} mean_excess={statistics.mean(s):+.2%}")
    print("\n⚠ Tiny, correlated, single-regime sample — this is an ESTIMATE/harness check, not a strategy verdict.")


if __name__ == "__main__":
    main()
