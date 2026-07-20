# A/B plan — adverse-momentum guard (`adv_mom`)

One pre-registered A/B for the failure modes found in `monday_t1_pit`
(`FAILURE_MODES_monday_t1_pit.md`). **Written before the test runs**, per anti-overfit rule 3.

## Why this one and not the other

The failure analysis produced two candidates: P1 (risk-gate window) and P2 (stop selecting on
LLM conviction). P1 goes first for three reasons:

1. **It can be tested for $0 on every stored cohort.** The risk gate applies penalties
   sequentially to `sig["conviction"]` and compares to the 0.55 threshold *at the end*
   (`nodes.py risk_gate_node`), and `backtest_runs.conviction` stores that final gating value.
   A new *penalty* is therefore replayable offline: `traded_new = traded_old AND
   (conviction_stored − penalty·[guard fires]) ≥ 0.55`. A penalty can only ever remove
   trades, never add them, so the replay is exact — no LLM calls, no new sweep.
2. **It targets the largest single loss in the book** (QCOM −23.9%), and the same class has
   now appeared three times (INTC −40.8% in #12, QCOM in #13, QCOM again here on clean data).
3. **P2 is confounded with `wf_30x_strong`.** Both are explanations of the same calibration
   defect; running them together would make neither interpretable. P2 waits.

## The mechanism (FROZEN — one formulation)

> **The priced-in guard, mirrored.** `priced_in_check` already computes the realized move over
> the shift window and penalises when it went **with** the signal ("the catalyst is in the
> price"). It has no branch for the opposite case. Add it: penalise when the move of the same
> size went **against** the proposed direction.

```
if |window_move_pct| >= ADVERSE_MOVE_PCT and move is AGAINST direction:
    conviction -= ADVERSE_MOVE_PENALTY
```

**Every constant is inherited, not chosen** — this is the point, and it is what keeps the
formulation honest given that the analysis that motivated it already saw the outcomes:

| Parameter | Value | Where it comes from |
|---|---|---|
| window | `SHIFT_WINDOW_DAYS` = 14d | the same window `priced_in_check` already uses |
| threshold | `ADVERSE_MOVE_PCT` = **8.0** | `PRICED_IN_MOVE_PCT`, unchanged |
| penalty | `ADVERSE_MOVE_PENALTY` = **0.2** | `PRICED_IN_PENALTY`, unchanged |

No threshold search, no window search. If the mirror at the inherited constants does not
work, the hypothesis is wrong — the answer is not a different number (that is the trap
rule 3 exists for).

The existing 180d `counter_trend` guard stays untouched: different horizon, different prior.
Its measured problem — it fired on **0 of 7** shorts — is *why* a short-window mirror is
needed, not a reason to retune it.

**Disclosure required by rule 1:** the 14d pre-move and the realized outcome of all 30 trades
were already inspected when this was formulated. Therefore **no window that has been mined
can accept this feature** — see the staging below.

## Implementation (small, flag-off by default)

| # | Item | Detail |
|---|---|---|
| I1 | Config | `LT_ADVERSE_MOVE_PCT` (8.0), `LT_ADVERSE_MOVE_PENALTY` (**0.0 = OFF**, rule 4) |
| I2 | Gate | ~6-line `else` branch beside the existing `same_way` block in `risk_gate_node`; reuses `priced_in_check`'s already-computed `window_move_pct` — no new data read |
| I3 | Flag text | prompts byte-identical; the guard is deterministic and post-synthesis, so LLM-cache keys are untouched |
| I4 | Replay tool | `ab_adverse_momentum.py`: recompute `traded` for stored rows, report book deltas + which trades were removed |
| I5 | Tests | fires against-direction only; silent when the move is with the signal (priced-in's job); silent below threshold; penalty 0 = exact no-op |

Replay caveat: `shift_window_days` is not stored per row in `backtest_runs`, so the offline
tool uses the config default (14d). Rows whose run used a per-ticker window replay
approximately; the stage-2 live sweep is exact.

## Staging and the criteria (frozen before any run)

**Stage 1 — do-no-harm replay, $0, all stored grids** (`monday_t1_pit`, `wf_30x_f12`,
`wf_30x_v0.2_deep`, …). These windows are burned; per rule 1 this stage can only **reject**.

Pre-registered predictions — the feature is REJECTED if any fails:

- **R1 (not inert).** The guard fires on **≥ 3%** of traded rows across the stored books.
  The 180d guard's fatal flaw was firing on nothing; if the mirror does the same, it dies
  here, for free, and that is a genuinely useful outcome.
- **R2 (hits the target class).** It removes the QCOM 2026-05-04 SHORT (14d pre-move +34.9%)
  and the INTC 2026-04-24 SHORT from #12. A guard that misses the trades it was built for
  fails the same way the #13 prompt did.
- **R3 (do-no-harm).** Mean 4w excess does not fall by more than **1pp** on any stored book,
  and the removed set is not overwhelmingly winners (removed-winner share ≤ 60%).
- **R4 (not a de-facto short ban).** It does not remove more than half of all SHORT trades.

**Stage 2 — acceptance, ~$4, unburned cohorts.** Mondays **2026-01-05 … 2026-04-20**
(~15 cohorts never used by any sweep), same 30 tickers, `--at 06:30 --fill d0`, fresh
`backtest_id` `adv_mom_oos`, run **twice**: guard OFF and ON. Prerequisite: the D1
knowledge-cutoff gate (Track C2) must land first so the window is provably post-cutoff —
otherwise "unburned" is undermined by model memory.

Acceptance requires **all**:

- **A1.** Mean 4w excess (guard ON) − (guard OFF) > **+1pp** (the #13 noise floor).
- **A2.** The improvement survives the **cohort-block** CI, not just the per-trade one.
- **A3.** Mechanism holds: the removed trades are concentrated in the adverse-momentum class,
  and the book's worst trade improves.
- **A4.** R3/R4 still hold out-of-sample.

Anything less keeps the flag OFF. "Not worse" keeps the incumbent (rule 4).

**Stage 3 — the arbiter.** If stage 2 accepts, enable live and record it as a forward
hypothesis; the forward book decides, as always.

## Cost and effort

| Stage | LLM cost | Effort |
|---|---|---|
| I1–I5 + stage 1 | **$0** | S (half a day) |
| Stage 2 | ~$4 (2 × ~390 runs, cache-warm on the OFF leg) | S once C2 exists |
| Stage 3 | 0 (rides the live batch) | — |

## What this plan deliberately excludes

- Tuning `ADVERSE_MOVE_PCT` / `PENALTY` if stage 1 or 2 disappoints — that is a new
  pre-registration, not an iteration of this one.
- Any prompt change (#13 proved the model cannot see the recent move from its context).
- Touching the conviction gate (P2) or the stop level in the same experiment.
