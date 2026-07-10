"""Offline eval harness (Modification Plan Phase 4).

Prompt/model changes must be measurable without a full backtest. Three evals, each
running the REAL node code path on frozen inputs from `evals/datasets/*.jsonl`:

  bucket_map        — weekly headline buckets → sentiment sign vs graph-derived truth
                      (the map model feeds detection: silent drift here silently
                      changes labels). The graph-sentiment seed is stripped from the
                      inputs so the model can't read the answer off the prompt.
  synthesis_matrix  — frozen synthesis states → direction must be consistent with the
                      news×fundamentals matrix cell (rule-checked, no judge, and
                      deliberately NOT scored against realized returns — that would be
                      fitting prompts to market noise).
  thesis_quality    — LLM-as-judge rubric over produced theses: grounded in supplied
                      evidence, concrete invalidation, cites a specific catalyst,
                      direction-consistent.

Datasets are versioned JSONL in-repo (reproducible, reviewable, no cloud dependency);
`--push-langsmith` optionally mirrors them to LangSmith for the UI.

    python -m evals.build_datasets            # (re)build datasets from the live DB
    python -m evals.run_evals                 # run all three, compare vs BASELINE.md
    python -m evals.run_evals --check-harness # prove the matrix eval catches an inverted matrix
    python -m evals.run_evals --update-baseline
"""
