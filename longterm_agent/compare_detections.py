"""Detection v1-vs-v2 comparison + v2 shift_library backfill in ONE controlled pass.

For every (ticker, as_of) on the grid it builds the corpus and (cached) weekly map
summaries ONCE, then computes shift metrics twice — v1 (weekly mean/std) and v2
(per-article daily median/MAD) — from identical inputs. Differences are therefore
PURELY the estimator change, not map-LLM re-rolls or corpus drift.

Writes:
  - v2 shift rows → shift_library under DETECTION v2 ("0.2.0")   ← the v2 backfill
  - v1 rows are NOT written (the stored, graded 0.1.0 library stays untouched)
  - a JSONL of every grid point + a markdown summary → longterm_agent/reports/

Also reports fresh-v1 vs STORED-v1 drift on overlapping grid points: the graph gains
AFFECTS edges over time, so a re-run of an old cohort can differ from what was stored —
that drift is a data-layer property, and printing it keeps it visible instead of silent.

    python -m longterm_agent.compare_detections --start 2026-04-30 --end 2026-07-09 --step 7
"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.agent.nodes import corpus_collection_node, background_profiling_node
from longterm_agent.tools.background_metrics import compute_shift_metrics
from longterm_agent.tools.shift_library_tool import write_shift_row
from news_agent.database.db_manager import DBManager

VERSION_STR = {1: "0.1.0", 2: "0.2.0", 3: "0.3.0"}
REPORTS = Path(__file__).parent / "reports"


def _metrics(state, cfg, neo, version):
    return compute_shift_metrics(
        bucket_summaries=state["bucket_summaries"],
        period_buckets=state["period_buckets"],
        as_of=state["as_of"],
        shift_window_days=cfg.SHIFT_WINDOW_DAYS,
        bucket_days=cfg.BUCKET_DAYS,
        ticker=state["ticker"],
        z_threshold=cfg.SENTIMENT_SHIFT_Z_THRESHOLD,
        min_abs_delta=cfg.MIN_ABS_DELTA,
        attention_spike_ratio=cfg.ATTENTION_SPIKE_RATIO,
        neo4j_manager=neo,
        metrics_version=version,
        articles=state.get("news_corpus"),
        sector_baseline=state.get("sector_baseline") if version >= 3 else None,
    )


def _shift_dict(m):
    """Deterministic BackgroundShift-shaped record from metrics (characterize=False path)."""
    return {
        "has_shifted": m["shift_flag"], "direction": m["direction"],
        "magnitude_z": m["sentiment"]["z"], "shift_score": m["shift_score"],
        "inflection_date": m["inflection_week"] if m["shift_flag"] else None,
        "candidate_catalysts": [], "reasoning": "",
    }


def main():
    ap = argparse.ArgumentParser(description="v1-vs-v2 detection comparison + v2 backfill")
    ap.add_argument("--tickers", nargs="+", help="default: full universe")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--versions", nargs=2, type=int, default=[1, 2], metavar=("OLD", "NEW"),
                    help="metric versions to compare; NEW's shifts are written to shift_library")
    ap.add_argument("--dry-run", action="store_true", help="no shift_library writes")
    args = ap.parse_args()
    v_old, v_new = args.versions

    cfg = LongTermAgentConfig()
    assert not cfg.CORPUS_EMBED_ENABLED, "comparison assumes the shared v1 corpus (embed off)"
    print(f"provenance: {cfg.provenance()}  |  comparing v{v_old} vs v{v_new} "
          f"(writing {VERSION_STR[v_new]} rows)")

    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=5)
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e}); corpus uses regex-only")

    tickers = args.tickers or cfg.get_universe()
    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    cohorts = []
    d = start
    while d <= end:
        cohorts.append(d)
        d += timedelta(days=args.step)

    # stored v1 flags for drift diagnostics
    stored_v1 = {}
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT ticker, as_of, direction FROM shift_library
                           WHERE agent_version='0.1.0'""")
            for tk, ao, dr in cur.fetchall():
                stored_v1[(tk, str(ao))] = dr

    REPORTS.mkdir(exist_ok=True)
    out_path = REPORTS / f"detection_v{v_old}_v{v_new}_{args.start}_{args.end}.jsonl"
    rows = []
    stats = {"runs": 0, "no_data": 0, "errors": 0,
             "v1_shifts": 0, "v2_shifts": 0, "both": 0, "v1_only": 0, "v2_only": 0,
             "direction_flips": 0, "stored_v1_overlap": 0, "stored_v1_drift": 0}

    with open(out_path, "w") as f:
        for as_of in cohorts:
            for ticker in tickers:
                stats["runs"] += 1
                try:
                    state = {"ticker": ticker.upper(), "as_of": as_of,
                             "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND, "messages": []}
                    state = corpus_collection_node(state, db, neo, cfg)
                    if state.get("no_data"):
                        stats["no_data"] += 1
                        continue
                    state = background_profiling_node(state, llm_map, None, cfg, db,
                                                      skip_reduce=True)
                    # v3 needs the sector baseline; compute it regardless of the env
                    # METRICS_VERSION so the comparison is self-contained.
                    if max(v_old, v_new) >= 3 and "sector_baseline" not in state:
                        from longterm_agent.tools.sector_sentiment import sector_baseline
                        try:
                            state["sector_baseline"] = sector_baseline(
                                ticker, as_of, cfg.LOOKBACK_DAYS_BACKGROUND, db, neo,
                                min_confidence=cfg.AFFECTS_MIN_CONFIDENCE)
                        except Exception as e:
                            print(f"  ⚠ sector baseline failed for {ticker}: {e}")
                            state["sector_baseline"] = None
                    m1 = _metrics(state, cfg, neo, v_old)
                    m2 = _metrics(state, cfg, neo, v_new)

                    f1, f2 = m1["shift_flag"], m2["shift_flag"]
                    stats["v1_shifts"] += f1
                    stats["v2_shifts"] += f2
                    stats["both"] += f1 and f2
                    stats["v1_only"] += f1 and not f2
                    stats["v2_only"] += f2 and not f1
                    if f1 and f2 and m1["direction"] != m2["direction"]:
                        stats["direction_flips"] += 1

                    key = (ticker.upper(), str(as_of.date()))
                    if key in stored_v1:
                        stats["stored_v1_overlap"] += 1
                        if not f1:  # stored says shift, fresh v1 says none → graph drift
                            stats["stored_v1_drift"] += 1

                    if f2 and not args.dry_run:
                        write_shift_row(db, ticker, as_of, VERSION_STR[v_new],
                                        _shift_dict(m2), {}, m2)

                    rec = {"ticker": ticker.upper(), "as_of": str(as_of.date()),
                           "v1": {"flag": f1, "dir": m1["direction"],
                                  "z": m1["sentiment"]["z"], "delta": m1["sentiment"]["delta"],
                                  "score": m1["shift_score"]},
                           "v2": {"flag": f2, "dir": m2["direction"],
                                  "z": m2["sentiment"]["z"], "delta": m2["sentiment"]["delta"],
                                  "score": m2["shift_score"],
                                  "n_recent_days": m2["sentiment"].get("n_recent_days"),
                                  "n_baseline_days": m2["sentiment"].get("n_baseline_days"),
                                  "coverage": m2["sentiment"].get("label_coverage"),
                                  "sparse": m2["sentiment"].get("sparse")},
                           "attention_spike": m1["attention"]["spike_ratio"],
                           "sector": m2.get("sector"),
                           "articles": state.get("article_count")}
                    rows.append(rec)
                    f.write(json.dumps(rec) + "\n")
                    if f1 != f2:
                        print(f"  ≠ [{as_of.date()}] {ticker}: v1={f1}({m1['direction']},"
                              f"z={m1['sentiment']['z']}) v2={f2}({m2['direction']},"
                              f"z={m2['sentiment']['z']},sparse={m2['sentiment'].get('sparse')})")
                except Exception as e:
                    stats["errors"] += 1
                    print(f"  [{as_of.date()}] {ticker}: ERROR {e}")

    decided = stats["runs"] - stats["no_data"] - stats["errors"]
    agree = decided - stats["v1_only"] - stats["v2_only"] - stats["direction_flips"]
    print(f"\n=== v{v_old} vs v{v_new} (same corpus, same summaries; "
          f"'v1'=old 'v2'=new in the JSONL) ===")
    print(f"grid: {stats['runs']} runs, decided {decided} (no_data {stats['no_data']}, errors {stats['errors']})")
    print(f"v1 shifts: {stats['v1_shifts']}  v2 shifts: {stats['v2_shifts']}  both: {stats['both']}")
    print(f"v1-only: {stats['v1_only']}  v2-only: {stats['v2_only']}  direction flips: {stats['direction_flips']}")
    if decided:
        print(f"flag agreement: {agree}/{decided} = {agree / decided:.1%}")
    print(f"stored-v1 drift (graph growth): {stats['stored_v1_drift']}/{stats['stored_v1_overlap']} "
          f"overlapping stored shifts no longer flagged by fresh v1")
    print(f"report → {out_path}")


if __name__ == "__main__":
    main()
