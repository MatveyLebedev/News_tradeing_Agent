# A/B plan — does the selection stage add value? (`sel_null`)

Pre-registered 2026-07-20, before any run. Companion to `AB_PLAN_adverse_momentum.md`
(`adv_mom`) and to the failure analysis `FAILURE_MODES_monday_t1_pit.md`.

## The measurement that motivated this

Every directional signal is graded, traded or not, so the counterfactual book is already in
the database at zero cost. Across three books, 4w, shifted directional signals only:

| book | selected by the gate | ALL signals, no conviction threshold |
|---|---|---|
| `monday_t1_pit` | n=30, **+0.18%** | n=42, **+1.36%** |
| `wf_30x_f12` | n=46, **+0.85%** | n=59, **+1.20%** |
| `wf_30x_v0.2_deep` | n=39, **+2.34%** | n=55, **+2.35%** |

**The gate never adds value in any book; in two of three it subtracts.** Splitting the
rejected signals by cause (using the stored `conviction_raw`, i.e. the LLM's number before
any machinery touched it):

| rejected because… | `monday_t1_pit` | `wf_30x_f12` | `…_deep` |
|---|---|---|---|
| the LLM itself was unsure (raw < 0.55) | n=3, +12.75% | n=1, −11.77% | n=4, −0.32% |
| **the machinery cut it** (raw ≥ 0.55) | n=9, **+1.48%** | n=12, **+3.62%** | n=12, **+3.27%** |

The first row is noise (n=1–4). The second is not the same story the failure analysis told:
it is not only LLM conviction that mis-selects — the **deterministic caps and penalties**
(no usable fundamentals, priced-in, earnings, counter-trend) removed trades that then did
better than the ones kept, in all three books.

*Caveats, stated up front:* n=9–12 per bucket, CIs overlap everywhere, windows are burned,
and this slice was chosen after seeing the data. This is grounds to **measure**, never
grounds to switch the live gate off.

## The structural diagnosis

`conviction` is one scalar doing three unrelated jobs before a single 0.55 comparison:

| job | today | should be |
|---|---|---|
| eligibility — shift? usable fundamentals? fresh data? | caps conviction to 0.50 | explicit boolean veto with a reason |
| risk adjustment — priced-in / earnings / counter-trend | penalties −0.1…−0.2 | adjustments, each one recorded |
| the model's belief in its own thesis | the raw number | a covariate, not a selector |

Consequences: you cannot tell *why* a trade was rejected (I had to infer it from
`raw` vs `final`, because the flags are not stored on backtest rows); no component is
separately measurable; and every new idea — including `adv_mom` — just adds another term to
the same soup.

---

## P2a — decompose and stamp (infrastructure, NO behaviour change, $0)

Store the gate's arithmetic instead of only its result. New `gate_trace` JSONB on
`backtest_runs` and `longterm_agent_results`:

```json
{"conviction_raw": 0.82, "conviction_final": 0.62, "gate_mode": "llm",
 "vetoes": [], "penalties": {"priced_in": -0.2},
 "table_inputs": {"shift_score": 0.56, "fundamentals_verdict": "contradicts"},
 "deep_nudge": 0.0}
```

Why this first: it makes **every future gate hypothesis replayable offline and free** — this
one, `adv_mom`'s stage 1 (exactly rather than approximately), and the `table` gate, whose
inputs are currently unrecoverable from backtest rows. Prompts untouched, decisions
identical, LLM-cache keys unchanged. Verified by asserting `conviction_final` reproduces the
existing stored value on a replay.

## P2b — the null test: is selection worth anything? (replay, ≈$0)

**Frozen challenger — ONE formulation.** `LT_SELECTION=vetoes_only`: trade every directional
signal that has a detected shift and passes the *eligibility vetoes* (no usable fundamentals,
stale data, no shift). **No conviction threshold, no risk penalties.** Incumbent stays the
current `llm` gate at 0.55. Flag defaults to the incumbent (rule 4).

This is deliberately the null, not a better gate. Testing `table` vs `llm` first would be
tuning the shape of a mechanism whose usefulness is unestablished; if selection turns out to
be worth nothing, that comparison is moot.

> **Measured 2026-07-20, and it changes how both stages must be run: a deep-enabled
> pipeline replay is NOT reproducible.** Re-running one cached cohort (QCOM 2026-05-04,
> same code, same `as_of`) produced **10 cache misses** and moved the decision from
> conviction 0.82 to 0.78 — across the 0.75 bucket boundary. The news corpus was
> identical (0 articles dated ≤ cohort were ingested after the sweep), so the drift comes
> from a non-news decision input; the Neo4j graph, which daily maintenance (`GRAPH_CLEANUP_TIME
> 04:00`, weekly merge) mutates *underneath* historical cohorts, is the leading candidate,
> and the deep-analysis ReAct loop amplifies any single differing tool output into a
> different chain. Not lookahead — a B0 reproducibility defect.
> **Consequences:** (1) both stages compute the challenger by **recomputing the gate offline
> from stored `gate_trace`**, never by re-running the pipeline; (2) stage 2 therefore needs
> only **ONE** sweep, not two legs — the gate is post-synthesis and deterministic, so
> incumbent and challenger are both derived from the same run, which also removes replay
> jitter from the comparison and halves the cost.

**Stage 1 — burned windows (`monday_t1_pit`, `wf_30x_f12`, `wf_30x_v0.2_deep`), reject-only.**
The challenger is REJECTED if any holds:

- **N1.** `vetoes_only` is worse than the incumbent by >1pp mean 4w excess on any book.
- **N2.** Trade count grows by more than **2×** (turnover/capital feasibility).
- **N3.** The worst trade of the book deteriorates by more than 5pp — i.e. the penalties were
  buying tail protection that the mean was hiding.

**Stage 2 — acceptance, unburned Mondays 2026-01-05…04-20**, fresh id `sel_null_oos`, both
legs. Accept only if **all**:

- **S1.** `vetoes_only` beats the incumbent by >**+1pp** mean 4w excess (the #13 noise floor).
- **S2.** The gap survives the **cohort-block** CI, not just the per-trade one.
- **S3.** It still holds **net of transaction costs** — a mandatory prerequisite here, because
  the challenger raises turnover ~40% (30→42 trades on `monday_t1_pit`). This promotes plan
  item D2 (costs as a third book) from "parked" to a **blocking dependency**.
- **S4.** N2/N3 still hold out-of-sample.

**Stage 3.** Forward book decides, as always.

## Sequencing — this changes the order announced for `adv_mom`

`adv_mom` adds **another penalty** to the gate, and the measurement above says the existing
penalties are, at best, not paying for themselves. Adding a term to an unvalidated mechanism
is premature, so:

```
P2a (stamp, $0) ──► P2b stage 1 ($0) ──► P2b stage 2 (~$4, needs D2 costs + C2 cutoff gate)
                                    └──► THEN adv_mom stage 1, now exact thanks to the stamps
```

`adv_mom`'s pre-registration stands unchanged — only its start moves. Under rule 3 that is
allowed: the formulation is not being edited, and no result has been seen for it.

If P2b shows selection is worth nothing, `adv_mom` is not merely delayed but **moot**, and the
real question becomes what should replace the selection stage — which is the tuning-loop
question (Phase 11), not another gate.

## Cost

| Item | LLM cost | Effort |
|---|---|---|
| P2a stamping + reconstruction assertion | $0 | S — **done 2026-07-20** |
| P2b stage 1 (offline recomputation from `gate_trace`) | $0 | S |
| P2b stage 2 (**one** sweep; both legs derived offline) | ~$2 | S, after D2 + C2 |

Note: existing rows carry no `gate_trace`, so stage 1 runs on books produced *after* P2a.
For the already-graded books the reconstruction falls back to `conviction_raw` vs final
(which distinguishes "LLM unsure" from "machinery cut it", but not which penalty fired) —
enough for the null test, not enough for `adv_mom`'s exact replay.

## Excluded from this experiment

- The `table` gate (a second formulation for the same defect — only after the null resolves).
- Any threshold move on 0.55, and any change to the stop level.
- Turning the gate off in live on stage-1 evidence: burned windows can only reject.
