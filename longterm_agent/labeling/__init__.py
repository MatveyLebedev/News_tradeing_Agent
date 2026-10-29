"""
Trace auto-labeling: labels a run from data that arrived AFTER it (prices, filings, later
news), stores them in Postgres and mirrors them into Langfuse as scores.

    trace_labels        the source of truth: one row per (run, node, label, labeler version).
                        Training exports and few-shot read only from here; a consumer at
                        `as_of` may see a label only if `label_available_at <= as_of`.
    trace_label_syncs   which labels a given Langfuse project already holds. Langfuse is a
                        mirror: a fresh project (new keys) gets every label re-sent, so
                        losing ClickHouse loses nothing.

A run is identified by `core.tracing.run_trace_key` — the same string its Langfuse trace id
is seeded from — so a label written weeks later lands on the right trace with no stored id.
Runs from before tracing existed get their trace rebuilt from `longterm_run_snapshots`.

Labelers (each a module with NAME, VERSION, SCORES and `collect(cur, ctx)`), run in order:
    results_adapter     restates the grader's columns; recomputes nothing.
    counterfactual      what the stock did after EVERY run (NEUTRAL too), priced like a trade.
    shift_persistence   did a shift persist — for the shifted runs the grader skips.
    decision_deltas     what risk_gate and reflection added (derived from counterfactual).
    map_buckets         weekly map sentiment vs that week's stock − SPY.
    cohorts             each weekly batch as a whole, scored on its Langfuse session.

    python -m longterm_agent.label_traces            # label + sync (the daily job)
"""
