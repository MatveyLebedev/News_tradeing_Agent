"""
Replay engine (Phase 5, detection-only) — populates shift_library by re-living the past.

Walks `as_of` weekly across the universe; at each point runs the lightweight detection
pass (corpus_collection → background_profiling → shift_detection) using ONLY data ≤ as_of
(no lookahead — the discipline is in every query), and writes each detected shift's
fingerprint to shift_library. The bucket-summary cache makes overlapping cohorts cheap.

This is NOT the full pipeline — no fundamentals/synthesis/risk_gate. It exists to (a) build
the analog case base and (b) test the core hypothesis (do detected shifts predict returns?).
Outcomes are filled later by grade_shifts.py.

    python -m longterm_agent.backfill_shift_library --start 2026-04-30 --end 2026-05-18 --step 7
"""
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.agent.nodes import (
    corpus_collection_node, background_profiling_node, shift_detection_node,
)
from longterm_agent.tools.shift_library_tool import write_shift_row
from news_agent.database.db_manager import DBManager


def detect_one(ticker, as_of, cfg, db, neo, llm_map, llm_reduce):
    """Run the detection-only pass for one (ticker, as_of). Returns the shift dict or None."""
    state = {"ticker": ticker.upper(), "as_of": as_of,
             "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND,
             "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": []}
    state = corpus_collection_node(state, db, neo, cfg)
    if state.get("no_data"):
        return None, state
    # Fast path: skip the reduce (profile summary) and the LLM shift-characterization.
    # The shift decision is deterministic from the map output, so the replay only needs the
    # (cached) map calls — no slow reasoning-model calls. Labels stay reproducible.
    state = background_profiling_node(state, llm_map, llm_reduce, cfg, db, skip_reduce=True)
    state = shift_detection_node(state, llm_reduce, cfg, neo, characterize=False)
    return state.get("background_shift"), state


def weekly_dates(start, end, step):
    d, out = start, []
    while d <= end:
        out.append(d)
        d += timedelta(days=step)
    return out


def main():
    ap = argparse.ArgumentParser(description="Replay past as_of dates to populate shift_library (detection-only)")
    ap.add_argument("--tickers", nargs="+", help="default: full universe")
    ap.add_argument("--start", required=True, help="earliest as_of (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help="latest as_of (YYYY-MM-DD)")
    ap.add_argument("--step", type=int, default=7, help="days between cohorts")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    cohorts = weekly_dates(start, end, args.step)

    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=5)
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e}); corpus uses regex-only")

    tickers = args.tickers
    if not tickers:
        from bloomberg_news_fetcher.price_fetcher import TOP_50_TICKERS
        tickers = TOP_50_TICKERS

    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    llm_reduce = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=6000)

    print(f"Replay: {len(tickers)} tickers × {len(cohorts)} cohorts "
          f"({args.start}→{args.end}, step {args.step}d)")
    stats = {"runs": 0, "shifts": 0, "no_data": 0, "errors": 0}
    for as_of in cohorts:
        for ticker in tickers:
            stats["runs"] += 1
            try:
                shift, state = detect_one(ticker, as_of, cfg, db, neo, llm_map, llm_reduce)
                if state.get("no_data"):
                    stats["no_data"] += 1
                    continue
                if shift and shift.get("has_shifted"):
                    write_shift_row(db, ticker, as_of, cfg.AGENT_VERSION, shift, {}, state.get("background_metrics", {}))
                    stats["shifts"] += 1
                    print(f"  [{as_of.date()}] {ticker}: SHIFT {shift.get('direction')} "
                          f"(score={shift.get('shift_score')})")
            except Exception as e:
                stats["errors"] += 1
                print(f"  [{as_of.date()}] {ticker}: ERROR {e}")

    print(f"\nReplay complete: {stats}")
    if neo:
        neo.close()


if __name__ == "__main__":
    main()
