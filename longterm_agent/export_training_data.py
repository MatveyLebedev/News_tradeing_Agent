"""
Export versioned training datasets (Modification Plan Phase 11) — join run snapshots
(inputs + LLM outputs, captured at run time) with realized outcomes (filled by graders)
into per-node JSONL files. This is the continuous job that turns forward operation into
tuning data; nothing here trains anything.

Datasets (one JSONL line per graded example, keyed by detection_version):
  synthesis        node inputs (profile/shift/fundamentals/analogs/technicals) +
                   emitted signal → realized excess (2w/4w/12w vs SPY, 4w vs sector ETF),
                   stopped-book exit, max adverse excursion
  fundamentals     the verdict the node gave → the NEXT filed quarter's realized picture
                   (aux_targets.fundamentals_realized)
  shift            the characterized shift → persisted / reverted (aux_targets.shift_outcome)

Hindsight guard: `inputs` come ONLY from the snapshot (state ≤ as_of by construction);
`targets` come ONLY from grader-filled columns. Nothing else is joined.

    python -m longterm_agent.export_training_data [--version 0.2.0] [--out-dir ...]
"""
import argparse
import json
import os
from datetime import date
from typing import Any, Dict, Optional

import psycopg2

from longterm_agent.config import LongTermAgentConfig


def _excess(fwd, bench):
    if fwd is None or bench is None:
        return None
    return round(fwd - bench, 4)


def build_examples(snap_state: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Optional[dict]]:
    """Pure: one snapshot + its graded results row → {dataset: example|None}."""
    sig = snap_state.get("filtered_signal") or snap_state.get("signal") or {}
    aux = row.get("aux_targets") or {}
    price_targets = {
        "excess_2w": _excess(row.get("fwd_return_2w"), row.get("benchmark_return_2w")),
        "excess_4w": _excess(row.get("fwd_return_4w"), row.get("benchmark_return_4w")),
        "excess_12w": _excess(row.get("fwd_return_12w"), row.get("benchmark_return_12w")),
        "excess_sector_4w": _excess(row.get("fwd_return_4w"), row.get("sector_benchmark_return_4w")),
        "sector_benchmark": row.get("sector_benchmark"),
        "stopped": {"fwd": row.get("fwd_return_stopped"), "reason": row.get("exit_reason"),
                    "days": row.get("exit_days")},
        "worst_position_return_4w": row.get("worst_position_return_4w"),
    }
    ident = snap_state.get("identity") or {}
    base = {"ticker": ident.get("ticker"), "as_of": ident.get("as_of"),
            "detection_version": (snap_state.get("provenance") or {}).get("detection_version")}

    out: Dict[str, Optional[dict]] = {"synthesis": None, "fundamentals": None, "shift": None}
    if price_targets["excess_4w"] is not None:
        out["synthesis"] = {**base, "inputs": {
            "background_profile": snap_state.get("background_profile"),
            "background_shift": snap_state.get("background_shift"),
            "fundamentals_trend": snap_state.get("fundamentals_trend"),
            "shift_analogs": snap_state.get("shift_analogs"),
            "technical_longterm": snap_state.get("technical_longterm"),
            "enriched_analysis": snap_state.get("enriched_analysis"),
        }, "output": sig, "targets": price_targets}
    fr = aux.get("fundamentals_realized")
    if fr and fr.get("label") not in (None, "unknown"):
        out["fundamentals"] = {**base,
                               "inputs": {"fundamentals_trend": snap_state.get("fundamentals_trend")},
                               "output": (snap_state.get("fundamentals_trend") or {}).get("verdict"),
                               "targets": fr}
    so = aux.get("shift_outcome")
    if so and so.get("label") not in (None, "unknown"):
        out["shift"] = {**base,
                        "inputs": {"background_shift": snap_state.get("background_shift"),
                                   "background_metrics": snap_state.get("background_metrics")},
                        "output": (snap_state.get("background_shift") or {}).get("direction"),
                        "targets": {**so, "excess_4w": price_targets["excess_4w"]}}
    return out


def main():
    ap = argparse.ArgumentParser(description="Export per-node training JSONL (Phase 11)")
    ap.add_argument("--version", default=None, help="detection version (default: config)")
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(__file__), "exports"))
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    version = args.version or cfg.DETECTION_VERSION
    os.makedirs(args.out_dir, exist_ok=True)

    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    cur.execute("""
        SELECT s.state,
               r.fwd_return_2w, r.benchmark_return_2w, r.fwd_return_4w, r.benchmark_return_4w,
               r.fwd_return_12w, r.benchmark_return_12w, r.sector_benchmark,
               r.sector_benchmark_return_4w, r.fwd_return_stopped, r.exit_reason, r.exit_days,
               r.worst_position_return_4w, r.aux_targets
        FROM longterm_run_snapshots s
        JOIN longterm_agent_results r
          ON r.agent_version = s.detection_version
         AND r.ticker = s.ticker AND r.as_of = s.as_of
        WHERE s.detection_version = %s AND r.outcome_filled_at IS NOT NULL
        ORDER BY s.as_of, s.ticker
    """, [version])
    cols = ("state", "fwd_return_2w", "benchmark_return_2w", "fwd_return_4w",
            "benchmark_return_4w", "fwd_return_12w", "benchmark_return_12w",
            "sector_benchmark", "sector_benchmark_return_4w", "fwd_return_stopped",
            "exit_reason", "exit_days", "worst_position_return_4w", "aux_targets")
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()

    stamp = date.today().isoformat()
    counts = {"synthesis": 0, "fundamentals": 0, "shift": 0}
    handles = {name: open(os.path.join(args.out_dir, f"{name}_{version}_{stamp}.jsonl"), "w")
               for name in counts}
    try:
        for row in rows:
            for name, ex in build_examples(row["state"], row).items():
                if ex is not None:
                    handles[name].write(json.dumps(ex, default=str) + "\n")
                    counts[name] += 1
    finally:
        for h in handles.values():
            h.close()
    print(f"exported (version={version}, graded snapshots={len(rows)}): "
          + "  ".join(f"{k}:{v}" for k, v in counts.items()) + f" → {args.out_dir}")
    return counts


if __name__ == "__main__":
    main()
