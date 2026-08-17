# A/B plan — directional trades require a confirmed cross-check (`confirms_only`)

Pre-registered 2026-08-09, **before any run of this feature**, per anti-overfit rule 3.
Companion to `AB_PLAN_selection_stage.md` (`sel_null`) and `AB_PLAN_adverse_momentum.md`
(`adv_mom`); like both, it is a deterministic gate change that replays offline from
`gate_trace`.

## What motivated it, and full disclosure

An event study over detected shifts (2026-08-09, grading-side, cohort-block CIs) split the
book by the stored fundamentals verdict and by the side the matrix took. Three cells that
the matrix does not license were present in the traded book and all lost: `LONG · ignores`
(n=16), `SHORT · ignores` (n=6), `SHORT · contradicts` (n=4). A fourth leak was found in the
same pass: 25 traded rows carry `verdict = unknown` with **no veto fired**, because
`_has_usable_fundamentals()` checks whether the income data *exists*, not whether the verdict
*landed*.

**Disclosure required by rule 1:** the realized outcomes of every cell — including the cells
this rule removes — were inspected before this was written. Therefore **no window that has
been mined can accept this feature.** Section "Staging" says what that leaves.

## The mechanism (FROZEN — one formulation)

> **A directional trade requires the news×fundamentals cross-check to have confirmed the
> shift.** `SYNTHESIS_MATRIX_RULES` makes the cross-check the thing that turns a detected
> shift into a position. A verdict of `confirms` is the only value that reports the check
> landed *and* agreed. `ignores` says the fundamentals are unrelated to the shift; `unknown`
> says no verdict was formed; `contradicts` says the check disagreed. None of the three is a
> confirmation, so none of the three licenses a direction.

```
if direction in ("LONG", "SHORT") and fundamentals_verdict != "confirms":
    veto("no_confirmed_crosscheck")
```

Nothing else changes: conviction, penalties, thresholds, prompts and the deterministic shift
label are untouched. The veto sits beside the existing `no_shift` / `no_usable_fundamentals`
vetoes in `risk_gate_node` and degrades the signal to FILTERED, preserving thesis and lean.

### Why not the wider variant

The first draft of this rule had two clauses: direction requires `confirms` **or**
`contradicts`, and SHORT requires `confirms`. It was rejected before registration. That
version keeps the `contradicts` cell on the long side only — a split which is not in the
matrix text and which I could only have derived from having seen that `contradicts · LONG`
measured positive and `contradicts · SHORT` measured negative on mined windows. That is one
researcher degree of freedom bought with hindsight, which is exactly what rule 3 forbids.
One clause, no per-side carve-out.

The cost of the narrow form is stated openly: the matrix's overreaction cell (negative shift
+ improving fundamentals → contrarian LONG) is a documented cell and this rule closes it.
That cell measured +8.20% mean 4w excess on mined windows — a number which, being post-hoc
on a burned grid, is **not admissible as a reason to keep the cell**, and symmetrically its
loss is not admissible as a reason to reject the rule (see Staging).

## The bite — measured before registration (mechanism, not outcome)

Across the stored books that carry `gate_trace` (P2a, 2026-07-20 onward) and have ≥20 traded
decisions — ten of the fifteen — deduped to one row per (backtest_id, ticker, as_of):

| | traded | kept (`confirms`) | removed |
|---|---:|---:|---:|
| all directional | 560 | 334 | **226 (40%)** |
| LONG | 363 | 160 | **203 (56%)** |
| SHORT | 197 | 174 | 23 (12%) |

**This is a long-book filter, not a short-book filter.** Shorts are already almost entirely
`confirms`; the rule cuts the long side by more than half. The
composition change is large enough that the book's beta and its comparison to a long-only
control both move; `STRATEGY_MONDAY_MIDDAY.md` §4.4 already flagged that control as defective
and this rule makes the defect worse, not better.

Per-book bite ranges from 25/69 (`holdout_mar_rich_off`) to 24/40 (`wf_fresh30_pit`, which
retains 3 of its 25 longs) — a spread wide enough that the rule's effect on any single book
says more about that book's config than about the rule.

### The input is an LLM label, and it is not stable

The veto is deterministic; the value it keys on is not. Of 567 (ticker, as_of) pairs with a
shift and a stamped verdict, **63 (11%) carry different verdicts across configs** — same
company, same date, different model or config, different cross-check. A gate whose bite is
config-dependent is a real risk to this feature and is registered here as a measurement to
report at every stage, not as something to be discovered later.

## Implementation (small, flag-off by default)

| # | Item | Detail |
|---|---|---|
| I1 | Config | `LT_REQUIRE_CONFIRMS` (**false = OFF**, rule 4) |
| I2 | Gate | veto branch in `risk_gate_node` beside `no_shift`; reads the verdict already in state; no new data read |
| I3 | Trace | append `no_confirmed_crosscheck` to `gate_trace.vetoes` so the arm replays offline |
| I4 | Replay tool | extend `ab_selection.py` — the removed set is a pure function of the stored verdict, so the replay is exact |
| I5 | Flag text | prompts byte-identical; the veto is deterministic and post-synthesis, so LLM-cache keys are untouched |
| I6 | Tests | fires on `ignores`/`unknown`/`contradicts`; silent on `confirms`; silent on NEUTRAL/NO_DATA; flag off = exact no-op |

## Staging and the criteria (frozen before any run)

**Stage 1 — verification replay, $0, all books with `gate_trace`.** These windows are burned
AND their per-cell outcomes were inspected while formulating this rule. Stage 1 is therefore
a *correctness* stage, not a filter, and this is stated up front rather than discovered
afterwards: a fall in stored-book mean excess is the **expected** consequence of closing a
cell that measured positive on those very windows, and is not grounds for rejection.

Rejected at stage 1 only if:

- **R1 (exactness).** The removed set is not exactly `{traded ∧ verdict ≠ confirms}`, or any
  row outside it changes state.
- **R2 (no side-effects).** Any change to conviction, penalties, NEUTRAL/NO_DATA rows, or to
  the flag-off replay reproducing the incumbent book byte-for-byte.
- **R3 (not a shutdown).** The ON book retains < 40% of directional trades across the pooled
  stored books.

Reported, not gated: per-book bite, LONG/SHORT split of the removed set, and the
config-instability share above.

**Stage 2 — acceptance, forward cohorts only.** There is no usable historical unburned
window: `news` is dense only from 2026-03, and every as_of from 2026-03-16 to 2026-07-27 with
usable news has been swept (32 distinct dates). Mid-week dates inside a mined window are at
best partially burned (`PREREG_MONDAY_MIDDAY_HOLDING_2026-07-29.md` §1.2c). Acceptance
therefore requires cohorts with **as_of > 2026-08-09**, accumulated forward, run twice
(flag OFF and ON) off the same decisions — the ON arm is a replay, so it costs nothing extra.

Acceptance requires **all**:

- **A1 (mechanism).** The removed class is exactly the non-`confirms` set; no other decision
  moves.
- **A2 (book).** 4w mean excess (ON) − (OFF) > **+1pp** (the #13 noise floor), and the
  improvement survives the **cohort-block** CI, not the per-trade one.
- **A3 (not tail-bought).** The kept long book's **median** 4w excess does not fall versus
  OFF. A rule that improves the mean while gutting the median has selected a tail, not a
  class.
- **A4 (still a book).** The ON arm retains ≥ 40% of directional trades.

Anything less keeps the flag OFF. "Not worse" keeps the incumbent (rule 4).

**Power — the binding constraint, stated before the run.** A 30-name weekly grid yields ~5.4
traded decisions per cohort; at a 40% bite that is ~2 removed and ~3 kept per cohort. A
±1pp book difference needs on the order of 15–20 fresh cohorts — **four to five months** of
forward accumulation, or proportionally less if the universe is widened to the 64-ticker set
the live path already runs. The live book alone (~3.2 traded/week) is slower still. If that
horizon is unacceptable, the honest response is to widen the universe, not to relax A2.

**Stage 3 — the arbiter.** If stage 2 accepts, enable live and record it as a forward
hypothesis; the forward book decides, as always.

## Cost

| Stage | LLM cost | Effort |
|---|---|---|
| I1–I6 + stage 1 | **$0** (pure replay) | S |
| Stage 2 | **$0** incremental (the ON arm replays the OFF arm's decisions) | accrues with the forward grid |
| Stage 3 | 0 (rides the live batch) | — |

## What this plan deliberately excludes

- Re-opening `contradicts` on either side if stage 1 or 2 disappoints — that is a new
  pre-registration, not an iteration of this one.
- Any threshold on `shift_score`, conviction, or the sub-verdicts. The rule keys on the
  headline verdict and nothing else.
- Touching the conviction gate (`sel_null`) or `adv_mom` in the same experiment. `sel_null`
  in particular is confounded with this rule — both change what becomes a trade — and must
  not be run in the same arm.
- Any prompt change, including asking the model to emit `confirms` more or less often.
