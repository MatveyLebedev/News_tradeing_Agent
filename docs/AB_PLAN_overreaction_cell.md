# A/B plan — close the overreaction cell (`no_overreaction`)

> **STAGE 3 ONLY IS BLOCKED, 2026-09-07 — the plan otherwise stands.** The live book narrowed
> to `(positive shift, confirms) → LONG` on expert advice (decision dated 2026-08-24, shipped
> in `0.11.0` behind `LT_LONG_CONFIRMS_ONLY`), which closes the cell this plan asks about.
>
> An earlier note here claimed the whole registration was blocked. That was wrong and is
> corrected: **stages 1 and 2 never used the live path.** Stage 2 is a sweep — one run with
> the full matrix, the challenger arm obtained by replay — and the flag defaults false, so
> backtests still produce both arms. The power figures below are already stated in sweep
> cohorts.
>
> What is genuinely blocked is **stage 3**: "if stage 2 accepts, enable live". A cell the live
> book has closed cannot be enabled live without reopening the book, which is a decision
> outside this plan. Also lost is live corroboration — no new overreaction trades will appear
> there, so the cross-check that stage 1 leans on (7 of 7 live overreaction trades carry a
> negative shift) cannot be extended.
>
> The registration therefore **stays open and runnable**. If stage 2 accepts, the result is a
> finding that the live book already acts on; if it rejects, the book was right for a reason
> nobody had measured. Either way it is worth knowing, and it costs a replay.

Pre-registered 2026-08-21, **before any run of this feature**, per anti-overfit rule 3.
Supersedes the strategy half of `AB_PLAN_matrix_cell.md`, whose conformance half shipped as
code in `75ae71c` and is no longer an experiment.

## Where this sits

`75ae71c` made the risk gate enforce the matrix: a LONG/SHORT is only taken when the
(shift sign, verdict) pair licenses that side. Three cells license anything —

    (positive, confirms) → LONG      (negative, confirms) → SHORT
    (negative, contradicts) → LONG   ← the overreaction cell

— and this plan asks whether the third one should exist at all. Nothing else changes, so the
challenger is exactly the incumbent minus one cell, and the arms differ by one boolean.

## The mechanism (FROZEN — one formulation)

> **A LONG requires the fundamentals to confirm the shift, not contradict it.** The matrix
> licenses `(negative shift, contradicts) → contrarian LONG` on the theory that a negative
> narrative over improving fundamentals is an overreaction, so the mispricing is downward and
> the trade is to buy it. The hypothesis is that this theory does not hold and the cell
> should license nothing, leaving `(positive, confirms) → LONG` as the only long entry.

```
MATRIX_CELLS minus {("negative", "contradicts"): {"LONG"}}
```

No threshold, no window, no weight. The feature is the deletion of one dictionary entry, and
if deleting it does not work the answer is not a softer version of the deletion.

### Why this cell and not a broader filter

Two broader variants were measured and are rejected as registrations, because the
decomposition shows they bundle effects of opposite sign:

* **`confirms`-only across the whole book** is arithmetically almost the same thing — the
  short side's only licensed cell is already `confirms`, so the filter bites only on longs.
  Registering the broad version would claim a short-side effect that cannot exist.
* **LONG-only** (drop the short leg) raises the mean at every horizon and *lowers the median*
  at every horizon (−0.43 / −1.05 / −6.29pp at 14/28/84d): it buys tail and sells
  reliability. It is a different hypothesis and is not registered here.
* **`LONG·confirms`** is the two stacked. On the live open book it decomposes to +3.51pp from
  dropping shorts and **−2.43pp** from the confirms filter — opposite signs inside one
  "result". Bundling them would make neither interpretable.

## Full disclosure, required by rule 1

Everything below was measured before this was written, on windows that are burned, and the
evidence is **two-sided**. No mined window can accept this feature.

**For.** Closing the cell (i.e. `confirms` inside the long leg) measured, on sweeps:

| horizon | Δ mean | Δ median |
|---|---|---|
| 14d | +2.03pp | +0.97pp |
| 28d | +3.07pp | +0.96pp |
| 84d | +7.01pp | +3.01pp |

and on the live realised book +1.70pp (2w, n=12) and +2.92pp (4w, n=7). It is the only one
of the candidate filters that lifts the **median** as well as the mean.

**The mechanism-level argument, which is stronger than any of those deltas.** Split by what
the stock did in the 21 sessions before the decision, `LONG · contradicts` returns +13.83%
(median +3.94) when the name came in **rising** and +2.74% (median **−1.88**, CI covering
zero) when it came in **falling**. The cell exists for the falling case — buy the overreaction
after the name is beaten down — and that is precisely the half that does not work. What works
is the half that is momentum with a disagreeing fundamental, which is not the stated
mechanism. (Pre-move sign is not shift sign; they correlate but are not the same, so this is
a motivation, not a measurement of the cell's premise.)

**Against.** On the live **open** book the five positions in this cell average **+5.77%**
(MU +12.9, T +8.2, ABBV +6.9, SBUX +2.5, SBUX −1.6) against a book at −0.18%, so closing the
cell would cost −2.43pp right now. On the heavily-mined sweep dates (≥12 sweeps) the same
filter measures **−2.25pp**. The cell's own pooled 4w return is +8.20% mean with CI
[+3.71, +14.09] — a class that, taken at face value, makes money.

**The honest summary of the evidence is that it flips sign between snapshots**, which is why
this is a registration and not a change.

## Implementation (small, flag-off by default)

| # | Item | Detail |
|---|---|---|
| I1 | Config | `LT_CLOSE_OVERREACTION` (**false = OFF**, rule 4) |
| I2 | Gate | `MATRIX_CELLS` is consulted through one helper; the flag drops the one entry. No new branch in `risk_gate_node`. |
| I3 | Trace | the existing `no_matrix_cell` veto reason covers it; `gate_trace.table_inputs.shift_direction` (shipped `75ae71c`) is what makes the arm replayable |
| I4 | Replay | `ab_selection.py` — the removed set is a pure function of `(direction, verdict)` on rows that already pass the shipped veto |
| I5 | Tests | flag off = exact no-op; flag on removes exactly `(negative, contradicts) → LONG`; no other cell moves |

## Staging and the criteria (frozen before any run)

**Stage 1 — replay, $0.** Unusually for this repo, stage 1 works on the *old* books despite
`shift_direction` not being stored before `75ae71c`: among rows that pass the shipped matrix
veto, a LONG with `contradicts` implies a negative shift, so the cell is identifiable from
`(direction, verdict)` alone. The approximation is that pre-`75ae71c` rows never passed that
veto, so a few `positive + contradicts` LONGs may be miscounted here — live says that class is
empty (7 of 7 live overreaction trades carry a negative shift), but it is an approximation and
is labelled as one.

Rejected at stage 1 only if:

- **R1 (exactness).** The removed set is not exactly `{traded ∧ LONG ∧ verdict=contradicts}`.
- **R2 (no side-effects).** Any change to short-side decisions, to conviction, or to the
  flag-off replay reproducing the incumbent book exactly.
- **R3 (not a shutdown).** The ON arm retains < 50% of LONG trades. Measured expectation:
  177 of 351 longs are `confirms`, so ~50% — this criterion is close and is registered anyway.

Stage 1 cannot accept. A fall in stored-book return is not a rejection either: the cell
measured positive on the very windows that informed this hypothesis.

**Stage 2 — acceptance, forward cohorts only,** `as_of > 2026-08-21`. Run OFF and ON off the
same decisions (the ON arm is a replay, so no extra LLM cost).

Acceptance requires **all**:

- **A1 (mechanism).** The removed class is exactly the overreaction cell; nothing else moves.
- **A2 (book).** 4w mean excess (ON) − (OFF) > **+1pp**, surviving the cohort-block CI.
- **A3 (median too).** The kept long book's **median** 4w excess does not fall. This is the
  criterion that distinguishes this hypothesis from LONG-only, which fails it by construction.
- **A4 (the stated mechanism holds).** Among removed trades, the ones that came in *falling*
  underperform the ones that came in *rising*. If the cell is being closed for the reason
  given, that ordering must be present out of sample; if the cell fails uniformly, the
  motivation was wrong even if the P&L improved.

Anything less keeps the flag OFF. "Not worse" keeps the incumbent (rule 4).

**Power — the binding constraint, stated before the run.** The cell is 174 of 634 traded sweep
decisions (27%) across 30 cohort dates, and 7 of 27 live traded decisions (26%), 5 of them
still open. A 30-name weekly grid yields ~5.4 traded decisions per cohort, so ~1.5 land in
this cell per cohort. A ±1pp read needs on the order of 15–20 fresh cohorts — **four to five
months**, or proportionally less on the 64-ticker live universe. Widening the universe is the
only honest way to shorten it; relaxing A2 is not.

**Stage 3 — the arbiter.** If stage 2 accepts, enable live and record it as a forward
hypothesis; the forward book decides.

## What this plan deliberately excludes

- Re-opening the cell under a condition (e.g. "only when the name came in rising"). That is
  the fitted variant this registration exists to avoid; it would be a new pre-registration.
- LONG-only, `confirms`-only, and `LONG·confirms` — see the decomposition above.
- Any change to the short leg, to conviction, to `sel_null` or to `adv_mom` in the same arm.
- Any claim from `docs/EVENT_STUDY_2026-08-20.md`, which is exploratory by its own first line.
