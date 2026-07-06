"""
CLI for the long-term agent (Phase 1: prints a company's news-background profile).

    python -m longterm_agent.run_longterm_agent --ticker NVDA --as-of 2026-06-12 --lookback 60
"""
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.agent.graph import create_longterm_graph
from longterm_agent.persistence import persist_run
from longterm_agent.llm_cache import maybe_enable_llm_cache
from news_agent.database.db_manager import DBManager


def main():
    ap = argparse.ArgumentParser(description="Long-term agent — news background profile (Phase 1)")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--lookback", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="Print the background profile as JSON")
    ap.add_argument("--no-persist", action="store_true", help="Skip writing to the results/shift tables")
    ap.add_argument("--trace", action=argparse.BooleanOptionalAction, default=True,
                    help="LangSmith tracing (default: ON; exports prompts to the LangSmith cloud). "
                         "Use --no-trace to disable, e.g. for offline/reproducible runs.")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    # Enable tracing BEFORE building LLMs/graph so callbacks attach to every call.
    if args.trace:
        cfg.enable_tracing()
    maybe_enable_llm_cache()  # LT_LLM_CACHE=true → reproducible & cheap re-runs (B0)
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d")
    lookback = args.lookback or cfg.LOOKBACK_DAYS_BACKGROUND

    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=5)
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e}); proceeding with ILIKE-only corpus")

    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    llm_reduce = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=8000)  # more headroom for reasoning tokens

    graph = create_longterm_graph(llm_map, llm_reduce, cfg, db, neo)

    init = {"ticker": args.ticker.upper(), "as_of": as_of, "lookback_days": lookback,
            "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": []}
    t0 = time.time()
    final = graph.invoke(init)
    elapsed = time.time() - t0

    if not args.no_persist:
        persist_run(db, final, cfg, execution_time=elapsed)

    print("\n" + "=" * 78)
    print(f"NEWS BACKGROUND — {args.ticker.upper()}  (as_of {args.as_of}, lookback {lookback}d)")
    print("=" * 78)

    if final.get("no_data"):
        print(f"NO_DATA: only {final.get('article_count', 0)} articles "
              f"(< {cfg.MIN_ARTICLES_FOR_BACKGROUND}). Company has too little news coverage.")
        return

    prof = final.get("background_profile", {})
    if args.json:
        print(json.dumps(prof, indent=2, default=str))
        return

    print(f"\nArticles analyzed: {final.get('article_count')}  "
          f"(provenance: {final.get('corpus_provenance')})")
    print(f"\nSummary:\n  {prof.get('summary', '(none)')}")

    print("\nDominant narratives:")
    for n in prof.get("dominant_narratives", []):
        print(f"  • [{n.get('direction')}] {n.get('label')} ({n.get('weeks_active')}w)")

    st = prof.get("sentiment_timeline", [])
    print(f"\nSentiment timeline (old→new): {st}")
    print(f"Attention timeline:          {prof.get('attention_timeline', [])}")

    if prof.get("recurring_risks"):
        print(f"\nRecurring risks:     {prof['recurring_risks']}")
    if prof.get("recurring_catalysts"):
        print(f"Recurring catalysts: {prof['recurring_catalysts']}")

    # --- Shift detection ---
    shift = final.get("background_shift", {})
    metrics = final.get("background_metrics", {})
    if shift:
        print("\n" + "-" * 78)
        sm = metrics.get("sentiment", {})
        att = metrics.get("attention", {})
        flag = "SHIFT DETECTED" if shift.get("has_shifted") else "no significant shift"
        print(f"BACKGROUND SHIFT: {flag}")
        if shift.get("has_shifted"):
            print(f"  direction={shift.get('direction')}  magnitude_z={shift.get('magnitude_z')}  "
                  f"inflection={shift.get('inflection_date')}")
        print(f"  metrics: sentiment z={sm.get('z')} (recent {sm.get('recent_mean')} vs "
              f"baseline {sm.get('baseline_mean')}), attention spike={att.get('spike_ratio')}x, "
              f"turnover={metrics.get('narrative_turnover')}")
        if metrics.get("new_stories"):
            print(f"  new stories: {[s.get('label') for s in metrics['new_stories']][:5]}")
        if shift.get("candidate_catalysts"):
            print(f"  catalysts: {shift['candidate_catalysts']}")
        if shift.get("reasoning"):
            print(f"  reasoning: {shift['reasoning']}")

    # --- Fundamentals analysis ---
    fund = final.get("fundamentals", {})
    tech = final.get("technical_longterm", {})
    if fund.get("has_statements"):
        trend = fund.get("trend", {})
        val = fund.get("valuation", {})
        print("\n" + "-" * 78)
        print(f"FUNDAMENTALS vs SHIFT: verdict = {str(trend.get('verdict','unknown')).upper()}")
        rev = fund.get("revenue_yoy")
        print(f"  revenue YoY={f'{rev*100:+.1f}%' if rev is not None else 'n/a'}  "
              f"PE={val.get('trailingPE')}  P/S={val.get('priceToSalesTrailing12Months')}  "
              f"gross_margin={val.get('grossMargins')}")
        eg = fund.get("expectation_gap")
        if eg:
            print(f"  analyst revisions (30d): {eg.get('up_30d')}↑/{eg.get('down_30d')}↓ → {eg.get('direction')}")
        if tech.get("available"):
            print(f"  technicals: {tech.get('trend')}, {tech.get('return_window_pct')}% over window, "
                  f"px {tech.get('last_close')} vs MA200 {tech.get('ma200')}")
        if trend.get("reasoning"):
            print(f"  reasoning: {trend['reasoning']}")

    # --- Analogs ---
    analogs = final.get("shift_analogs", {})
    if analogs:
        print("\n" + "-" * 78)
        if analogs.get("n_analogs"):
            print(f"ANALOGS: {analogs['n_analogs']} similar past shifts | median 4w return "
                  f"{analogs.get('median_fwd_return_4w')} | share positive {analogs.get('share_positive')}")
            for c in analogs.get("nearest_cases", []):
                print(f"  · {c}")
        else:
            print(f"ANALOGS: {analogs.get('fundamentals_matched_note', 'none')}")

    # --- Final signal ---
    sig = final.get("signal", {})
    if sig:
        passed = final.get("filtered_signal") is not None
        print("\n" + "=" * 78)
        print(f"SIGNAL: {sig.get('direction')}  conviction={sig.get('conviction')}  "
              f"horizon={sig.get('horizon')}  [{'PASSED risk gate' if passed else 'FILTERED'}]")
        print(f"\nThesis:\n  {sig.get('thesis','')}")
        if sig.get("supporting_factors"):
            print("\nSupporting factors:")
            for f in sig["supporting_factors"]:
                print(f"  + {f}")
        if sig.get("key_risks"):
            print("\nKey risks:")
            for r in sig["key_risks"]:
                print(f"  - {r}")
        if sig.get("invalidation_conditions"):
            print("\nInvalidation conditions (what kills the thesis):")
            for ic in sig["invalidation_conditions"]:
                print(f"  ! {ic}")
        if final.get("risk_flags"):
            print(f"\nRisk flags: {final['risk_flags']}")
    print()


if __name__ == "__main__":
    main()
