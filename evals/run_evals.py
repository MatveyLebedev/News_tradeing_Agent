"""Run the offline evals against current prompts/models; compare vs evals/BASELINE.md.

    python -m evals.run_evals                    # all three, fail on regression
    python -m evals.run_evals --only synthesis_matrix --limit 8
    python -m evals.run_evals --update-baseline  # record current scores as the baseline
    python -m evals.run_evals --check-harness    # inverted matrix must TANK the score
    python -m evals.run_evals --push-langsmith   # mirror datasets to LangSmith (optional)

Exit codes: 0 ok / skipped, 1 = regression beyond --tolerance or harness check failed.
Needs OPENROUTER_API_KEY (skips gracefully without it — for CI forks); the datasets are
frozen JSONL in-repo, so NO database is needed at eval time.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

from pydantic import BaseModel

from evals.matrix import score_matrix_case

DATASETS = Path(__file__).parent / "datasets"
BASELINE = Path(__file__).parent / "BASELINE.md"


def _load(name: str) -> list:
    path = DATASETS / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _sign(x: float, dead_zone: float = 0.1) -> int:
    return 1 if x > dead_zone else -1 if x < -dead_zone else 0


# --- bucket_map ---------------------------------------------------------------
def eval_bucket_map(cfg, rows, limit=None):
    """Real _map_bucket on frozen headline buckets; metric = sentiment-sign accuracy."""
    from longterm_agent.agent.nodes import _map_bucket
    llm = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    hits, details = [], []
    for row in rows[:limit]:
        arts = {a["id"]: a for a in row["articles"]}
        bucket = {"index": 0, "start": datetime.fromisoformat(row["week_start"]),
                  "end": datetime.fromisoformat(row["week_end"]), "count": row["count"],
                  "article_ids": list(arts), "affects_sentiments": []}
        out = _map_bucket(llm, bucket, arts)
        got = _sign(out.get("sentiment", 0.0))
        ok = got == row["expected_sign"]
        hits.append(ok)
        details.append(f"  {'✓' if ok else '✗'} {row['id']}: got {out.get('sentiment'):+.2f} "
                       f"expected sign {row['expected_sign']:+d}")
    return {"score": round(mean(hits), 3) if hits else None, "n": len(hits), "details": details}


# --- synthesis_matrix -----------------------------------------------------------
def eval_synthesis_matrix(cfg, rows, limit=None, inverted=False):
    """Real synthesis_node on frozen states; metric = matrix-consistency rate.
    `inverted=True` swaps LONG/SHORT in the prompt rules — the harness sanity check."""
    from longterm_agent.agent import nodes
    llm = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=8000)

    original = nodes.SYNTHESIS_MATRIX_RULES
    if inverted:
        nodes.SYNTHESIS_MATRIX_RULES = (original
                                        .replace("LONG", "@@").replace("SHORT", "LONG")
                                        .replace("@@", "SHORT"))
    hits, details = [], []
    try:
        for row in rows[:limit]:
            state = dict(row["state"])
            state["as_of"] = datetime.fromisoformat(str(state["as_of"])[:10])
            state["messages"] = []
            nodes.synthesis_node(state, llm, cfg)
            sig = state.get("signal") or {}
            cell = row["cell"]
            ok = score_matrix_case(cell["has_shifted"], cell["direction"], cell["verdict"],
                                   sig.get("direction"), sig.get("conviction"),
                                   threshold=cfg.LONGTERM_CONFIDENCE_THRESHOLD)
            if ok is None:
                continue
            hits.append(ok)
            details.append(f"  {'✓' if ok else '✗'} {row['id']}: cell={cell} → "
                           f"{sig.get('direction')} conv={sig.get('conviction')}")
    finally:
        nodes.SYNTHESIS_MATRIX_RULES = original
    return {"score": round(mean(hits), 3) if hits else None, "n": len(hits), "details": details}


# --- thesis_quality --------------------------------------------------------------
class ThesisJudgment(BaseModel):
    grounded_in_inputs: bool
    concrete_invalidation: bool
    cites_specific_catalyst: bool
    direction_consistent: bool
    notes: str = ""


_JUDGE_PROMPT = """You are auditing ONE trading thesis produced by an analysis pipeline.
You are given exactly the EVIDENCE the pipeline had, and its thesis. Judge four criteria:
- grounded_in_inputs: every factual claim in the thesis traces to the evidence below
  (no invented numbers, events, or entities).
- concrete_invalidation: at least one invalidation condition is a specific, observable
  event/level (not "if things get worse").
- cites_specific_catalyst: the thesis names at least one concrete catalyst/event from
  the evidence, not just "positive news flow".
- direction_consistent: the thesis text argues the SAME direction as the signal.

EVIDENCE:
{inputs}

SIGNAL: {direction} (conviction {conviction})
THESIS: {thesis}
INVALIDATION CONDITIONS: {invalidation}
"""


def eval_thesis_quality(cfg, rows, limit=None):
    # Cheap model as judge (cost); known caveat: same provider family as the generator,
    # so use this as a REGRESSION metric, not an absolute quality claim.
    llm = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    scores, details = [], []
    for row in rows[:limit]:
        prompt = _JUDGE_PROMPT.format(
            inputs=json.dumps(row["inputs"], indent=1, ensure_ascii=False),
            direction=row["direction"], conviction=row["conviction"],
            thesis=row["thesis"], invalidation=row["invalidation_conditions"])
        j: ThesisJudgment = llm.with_structured_output(ThesisJudgment).invoke(prompt)
        crit = [j.grounded_in_inputs, j.concrete_invalidation,
                j.cites_specific_catalyst, j.direction_consistent]
        scores.append(mean(crit))
        details.append(f"  {row['id']}: {sum(crit)}/4 {j.notes[:80]}")
    return {"score": round(mean(scores), 3) if scores else None, "n": len(scores),
            "details": details}


# --- baseline ---------------------------------------------------------------------
def read_baseline() -> dict:
    if not BASELINE.exists():
        return {}
    out = {}
    for m in re.finditer(r"^\| (\w+) \| ([\d.]+) \| (\d+) \|", BASELINE.read_text(), re.M):
        out[m.group(1)] = float(m.group(2))
    return out


def write_baseline(results: dict, cfg):
    prov = cfg.provenance()
    lines = [
        "# Eval baseline",
        "",
        f"Recorded {datetime.now().date()} · model `{prov['model']}` · map `{prov['map_model']}` "
        f"· pipeline `{prov['pipeline_version']}` · git `{prov['git_revision']}`",
        "",
        "| eval | score | n |",
        "|------|-------|---|",
    ]
    for name, r in results.items():
        if r["score"] is not None:
            lines.append(f"| {name} | {r['score']} | {r['n']} |")
    lines += ["", "Update with `python -m evals.run_evals --update-baseline` after a",
              "deliberate, reviewed prompt/model change — never to make CI pass."]
    BASELINE.write_text("\n".join(lines) + "\n")
    print(f"baseline written → {BASELINE}")


def push_langsmith():
    from langsmith import Client
    client = Client()
    for name in ("bucket_map", "synthesis_matrix", "thesis_quality"):
        rows = _load(name)
        if not rows:
            continue
        ds_name = f"longterm-{name}"
        try:
            ds = client.create_dataset(ds_name)
        except Exception:
            ds = client.read_dataset(dataset_name=ds_name)
        client.create_examples(
            inputs=[{"case": r} for r in rows],
            outputs=[{} for _ in rows], dataset_id=ds.id)
        print(f"  pushed {len(rows)} → LangSmith dataset '{ds_name}'")


EVALS = {"bucket_map": eval_bucket_map, "synthesis_matrix": eval_synthesis_matrix,
         "thesis_quality": eval_thesis_quality}


def main():
    ap = argparse.ArgumentParser(description="Offline evals vs BASELINE.md")
    ap.add_argument("--only", choices=list(EVALS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="max allowed score drop vs baseline before failing")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--check-harness", action="store_true",
                    help="run synthesis_matrix normal vs inverted-matrix; inverted must tank")
    ap.add_argument("--push-langsmith", action="store_true")
    args = ap.parse_args()

    from longterm_agent.config import LongTermAgentConfig
    cfg = LongTermAgentConfig()
    if not cfg.OPENROUTER_API_KEY:
        print("no OPENROUTER_API_KEY → evals skipped")
        return 0

    if args.push_langsmith:
        push_langsmith()
        return 0

    if args.check_harness:
        rows = _load("synthesis_matrix")
        subset = [r for r in rows if r["source"] == "synthetic"]
        normal = eval_synthesis_matrix(cfg, subset)
        inverted = eval_synthesis_matrix(cfg, subset, inverted=True)
        print(f"harness check: normal={normal['score']} inverted={inverted['score']}")
        if normal["score"] is None or inverted["score"] is None:
            print("❌ harness check inconclusive (no scorable cases)")
            return 1
        # The eval must be able to tell a correct matrix from an inverted one.
        ok = inverted["score"] <= normal["score"] - 0.3
        print("✅ harness detects an inverted matrix" if ok
              else "❌ harness FAILED to punish an inverted matrix")
        return 0 if ok else 1

    names = [args.only] if args.only else list(EVALS)
    baseline = read_baseline()
    results, failed = {}, []
    for name in names:
        rows = _load(name)
        if not rows:
            print(f"{name}: no dataset (run `python -m evals.build_datasets`) — skipped")
            continue
        r = EVALS[name](cfg, rows, limit=args.limit)
        results[name] = r
        base = baseline.get(name)
        delta = f" (baseline {base}, Δ{r['score'] - base:+.3f})" if base is not None and r["score"] is not None else ""
        print(f"{name}: {r['score']} (n={r['n']}){delta}")
        if args.verbose:
            print("\n".join(r["details"]))
        if base is not None and r["score"] is not None and r["score"] < base - args.tolerance:
            failed.append(name)

    if args.update_baseline and results:
        write_baseline(results, cfg)
    if failed:
        print(f"\n❌ regression beyond tolerance in: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
