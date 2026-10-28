"""
Label runs from data that arrived after them, and mirror the labels into Langfuse as scores
(trace auto-labeling — see longterm_agent/labeling).

Runs daily right after grading (auto_longterm_agent.Ops.label_traces). Idempotent: an
unchanged label is neither rewritten in Postgres nor re-sent to Langfuse, and a labeled run
missing from Langfuse gets its trace rebuilt from its snapshot.

    python -m longterm_agent.label_traces                  # label + sync
    python -m longterm_agent.label_traces --no-sync        # Postgres only
    python -m longterm_agent.label_traces --rebuild-all    # + a trace for EVERY snapshot
"""
import argparse
import traceback
from datetime import datetime, timezone
from typing import Dict

from core import tracing
from core.db import get_conn
from core.settings import get_settings
from longterm_agent.config import LongTermAgentConfig
from longterm_agent.labeling import results_adapter
from longterm_agent.labeling.base import ScoreSpec
from longterm_agent.labeling.store import ensure_tables, lf_target, upsert_labels

LABELERS = (results_adapter,)


def score_specs() -> Dict[str, ScoreSpec]:
    specs: Dict[str, ScoreSpec] = {}
    for lab in LABELERS:
        clash = specs.keys() & lab.SCORES.keys()
        assert not clash, f"{lab.NAME} redefines scores {sorted(clash)}"
        specs.update(lab.SCORES)
    return specs


def rebuild_all(cur, lf, limit: int = None) -> int:
    """A Langfuse trace for every snapshot that has none — the HOLD runs too, so the whole
    history can be browsed and annotated, not only the graded trades."""
    from longterm_agent.labeling import langfuse_sync as sync

    now, built = datetime.now(timezone.utc), 0
    for snap in sync.iter_snapshots(cur):
        if limit and built >= limit:
            break
        trace_id = tracing.run_trace_id("live", snap["detection_version"], snap["ticker"],
                                        snap["as_of"])
        if sync.run_window(snap)[1] >= now - sync.REBUILD_MIN_AGE:
            continue
        if sync.observation_index(lf.api, trace_id):
            continue
        sync.rebuild_trace(lf, snap)
        built += 1
        if built % 50 == 0:
            lf.flush()
            print(f"  … {built} traces rebuilt")
    lf.flush()
    return built


def main(argv=None) -> Dict[str, object]:
    ap = argparse.ArgumentParser(description="Label runs from later data; mirror to Langfuse")
    ap.add_argument("--no-sync", action="store_true", help="write Postgres labels only")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="never rebuild a missing trace from its snapshot")
    ap.add_argument("--rebuild-all", action="store_true",
                    help="rebuild a trace for every snapshot that has none, labeled or not")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the traces --rebuild-all writes in one run")
    args = ap.parse_args(argv)

    cfg = LongTermAgentConfig()
    stats: Dict[str, object] = {}
    with get_conn(cfg.get_db_config()) as conn:
        with conn.cursor() as cur:
            ensure_tables(cur)
            for lab in LABELERS:
                stats[lab.NAME] = upsert_labels(cur, lab.collect(cur))
            conn.commit()
            print(f"labels: {stats}")
            if args.no_sync:
                return stats
            # The scheduler already initialised tracing; a CLI run starts it here. Off
            # (LT_TRACE=false or no LANGFUSE_*) means Postgres-only, which is not an error.
            if tracing.client() is None and not tracing.init(default_environment="live"):
                stats["sync"] = "off"
                return stats
            from longterm_agent.labeling import langfuse_sync as sync

            lf = tracing.client()
            target = lf_target(get_settings().langfuse_public_key)
            try:
                specs = score_specs()
                config_ids = sync.ensure_score_configs(lf.api, specs)
                if args.rebuild_all:
                    stats["rebuilt_all"] = rebuild_all(cur, lf, args.limit)
                stats["sync"] = sync.push(cur, lf, target, specs, config_ids,
                                          rebuild=not args.no_rebuild)
                conn.commit()
            except Exception as e:
                # Langfuse is a mirror: unreachable today, the next run re-sends everything
                # this one did not mark. Postgres labels are already committed above.
                try:
                    conn.rollback()
                except Exception:
                    pass  # the connection itself died; there is nothing left to roll back
                stats["sync"] = f"failed: {type(e).__name__}: {e}"
                print(f"  ⚠ Langfuse sync failed (labels kept in Postgres): {e}")
                traceback.print_exc(limit=3)
            finally:
                lf.flush()
    print(f"sync: {stats.get('sync')}")
    return stats


if __name__ == "__main__":
    main()
