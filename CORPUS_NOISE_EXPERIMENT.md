# Corpus Noise Experiment — hypothesis registration

**Status: REGISTERED, not started** · registered 2026-07-22, before any runs
(hindsight guard: hypotheses and success criteria are frozen here pre-outcome).

**Registry entry:** `docs/BACKTEST_LEDGER.md` → Queued / future →
`wf_30x_nonoise` (pre-registered 2026-07-22). The ledger entry is the binding
pre-registration (ONE formulation, frozen mechanism predictions, burned-grid
protocol); this file holds the extended rationale. H-A is queued there as
`wf_30x_nonoise`; H-B is registered here only — it becomes a run with its own
ledger id if/when the graph-integration question is resolved.

Provenance: prompted by a colleague's external news-filtering pipeline spec
(`NEWS_FILTERING_PIPELINE.md`, not in this repo — the emulator project this
service now feeds via `news_api/`). Two of its ideas transfer to the long-term
agent as testable hypotheses; the rest (keyword funnel, category tags) is
emulator-specific and is NOT adopted.

Relationship to existing work:
- `SIGNIFICANCE_EXPERIMENT.md` — *empirical* filter (label = price move).
  This experiment tests an *editorial* filter (label = "events and official
  actions only, no third-party opinions"). Orthogonal axes; the flags table
  below doubles as a cross-validation set for the significance experiment
  (does "analyst would cite it" correlate with realized moves?).
- `MODIFICATION_PLAN.md` Phase 11 — `news_flags` columns become candidate
  features for the outcome-based tuning loop.

---

## H-A. Opinion filter over the company corpus

**Hypothesis:** removing third-party opinion items (op-eds, strategist views,
predictions, listicles, pure price recaps) from the corpus that
`longterm_agent/tools/news_corpus_tool.py` feeds into background profiling
(1) does not degrade — or improves — shift-detection quality, and
(2) reduces map-reduce token cost proportionally to the share removed.

**Registered contra-hypothesis (genuinely two-sided):** narrative shifts often
surface in commentary *before* official events; the filter may delay or lose
early detections. If arm B loses detections that arm A caught AND those
detections graded well, the contra-hypothesis wins and the filter is rejected.

**Sketch:** one-off Stage-2-style classification pass (cheap model, batches of
25, headline + first 300 chars — PIT-safe: publication-time data only) into a
side table `news_flags (news_id, is_opinion, relevance, tags)`; the filter is
a single JOIN in the corpus query, behind a flag.

## H-B. Event-level dedup with `dup_count` — belongs in the graph

**Hypothesis:** collapsing same-event coverage (N journals → 1 corpus row +
`dup_count`) preserves detection quality while cutting corpus size/tokens;
`dup_count` (coverage intensity) is itself a usable importance feature.

**Architectural decision (registered per discussion 2026-07-22):** dedup must
NOT be a new Jaccard clustering in Postgres — the graph already does this at
event formation: Article →`SOURCED_FROM`→ Event (canonical_id match → cosine
merge), and `Story.article_count` already is the attention signal. The real
gap: the corpus recall paths (ILIKE, pgvector embed) bypass the graph and
reintroduce duplicates that the AFFECTS path had already collapsed. So H-B is
an *integration* task — corpus rows keyed by Event where a graph link exists,
`dup_count` = SOURCED_FROM fan-in — not a new dedup algorithm.

**Registered risk:** shift detection may implicitly rely on raw coverage
volume; dedup-with-count must not lose that signal (weigh buckets by
`dup_count`, don't drop it).

---

## Design (frozen at registration; details TBD before first run)

Arms on identical tickers × `as_of` dates via the existing batch runner:
**A** baseline · **B** = A + opinion filter · **C** = B + event dedup.

Metrics (all three arms):
1. Detection flip rate vs baseline (`compare_detections.py`) — flips must be
   explainable, not random churn.
2. Eval scores vs `evals/BASELINE.md` (bucket_map, synthesis_matrix,
   thesis_quality).
3. Graded P&L on matured cohorts (`profit_compute`) where available.
4. Token cost per run.
5. Corpus-size distribution — the `< 8 articles → NO_DATA` exit must not
   swallow tickers (guardrail: NO_DATA rate may not rise vs arm A).

**Success criteria (frozen):** an arm wins if it is ≥ baseline on (1)–(3)
within noise and strictly better on (4), without violating (5). H-A and H-B
are judged independently (B vs A, C vs B).

**PIT guards:** flags computed from publication-time fields only; one
classification pass reused across arms; no reclassification after outcomes
are known.
