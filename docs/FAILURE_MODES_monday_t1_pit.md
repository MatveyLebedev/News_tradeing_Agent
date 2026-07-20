# What the agent got wrong — `monday_t1_pit` (clean, post-PIT-fix), 2026-07-20

30 graded 4w trades (16 losers / 14 winners), mean excess +0.18%. This is the first
failure analysis on **leak-free** data, so unlike the pre-audit post-mortems it is not
explaining artefacts.

> **Protocol status:** everything below was found by looking at this window's results.
> Under the burned-grid rule (anti-overfit §1) nothing here can be *accepted* on
> `monday_t1_pit` — the proposals must be pre-registered and tested on cohorts that
> were not mined to produce them (forward book, or the unexamined Jan–Apr 2026 window).
> n=30 in 3 cohorts; treat every split below as a lead, not a measurement.

---

## Finding 1 — conviction is anti-predictive, and it survived the PIT fix ⚠ strongest

| bucket | n | mean excess | win |
|---|---|---|---|
| conviction ≥ 0.70 | 11 | **−2.49%** | 45% |
| conviction < 0.70 | 19 | **+1.73%** | 47% |

The two worst trades in the book (QCOM −23.9%, CRM −8.4%) both carried **0.82** — the top
bucket. The best (ORCL +35.6%) carried 0.58.

This reproduces ledger #4's finding, and that is the important part: #4 could have been an
artefact of the leaks, since the deep-analysis nudge (±0.2) feeds conviction and the deep
path was where every hole lived. It wasn't. **The model is most confident exactly where it
is most wrong**, and that number is what the 0.55 trade gate selects on. The damage is at
the *selection* stage, not the analysis stage.

## Finding 2 — the risk gates cannot see the danger they exist for

Worst trade: **QCOM SHORT, as_of 2026-05-04, conviction 0.82 → stock +29.2%, excess −23.9%.**
It had run **+34.9% in the 20 days before the decision**. Both existing guards stayed silent,
and both for structural reasons:

- `priced_in_check` (`LT_PRICED_IN_MOVE_PCT=8`) only fires when price already moved **with**
  the signal ("the catalyst is in the price"). QCOM moved hard *against* the proposed short,
  which the guard is not built to notice.
- `counter_trend` (`LT_COUNTER_TREND_PCT=50`) measures the **180-day** window. QCOM's 180d
  trend was **−1.5%** — the violent move was recent and invisible at that horizon.

Across all 7 shorts the 180d trends span −29%…+29%: **the counter-trend guard fired zero
times.** At its current parameterization it is effectively dead code on real candidates.

Pre-decision 20d move vs realized 4w, all shorts:

| ticker | as_of | 20d pre-move | realized 4w | excess |
|---|---|---|---|---|
| QCOM | 05-04 | **+34.9%** | +29.2% | **−0.239** |
| META | 04-27 | +17.8% | −9.0% | +0.136 |
| TSLA | 05-04 | +10.9% | +6.6% | −0.012 |
| C | 04-27 | +9.0% | −2.2% | +0.067 |
| CRM | 05-04 | +6.4% | +13.8% | −0.084 |
| DIS | 05-04 | +1.9% | +0.1% | +0.052 |
| CRM | 04-27 | −3.7% | +1.0% | +0.035 |

Honest reading: the one catastrophic loss had extreme adverse pre-momentum, but META (+17.8%
pre-move) was the book's *best* short. n=7 — the tail case is suggestive, the relationship
is not established.

## Finding 3 — beta is NOT the explanation (hypothesis tested and rejected)

The prior hypothesis was that losses were mechanical: cohort 05-04 had SPY +5.3%, and the
book was long defensives / short high-beta, so excess-vs-SPY would punish it regardless of
judgement. Decomposing each trade into a beta part `sign·(β−1)·SPY` and a residual:

```
mean excess     +0.0018  (sd 0.1082)
mean beta-part  −0.0032  (sd 0.0289)
mean alpha-part +0.0050  (sd 0.1039)
→ beta explains ~4% of excess dispersion
```

**The losses are real stock-specific misses, not benchmark mechanics.** One caveat worth
keeping: PEP and KO priced with β ≈ −0.03 to SPY over the window, so those two trades carried
a mechanical −5pp drag each — material per-trade, immaterial for the book.

## Finding 4 — short-side results are unstable at this n

Cohort 04-27: shorts **+7.96%** (n=3). Cohort 05-04: shorts **−7.07%** (n=4), 3 of 4 stopped.
Sign flip between adjacent weeks. All 4 stop-outs in the book were high-conviction (0.62–0.82).
There is no stable short skill visible either way — this is an observation to watch, not a
basis for changing the direction mix.

---

## What follows — proposals, as general mechanisms

Each needs its own frozen pre-registration in `BACKTEST_LEDGER.md` before any sweep, and can
only be accepted on unburned cohorts.

**P1 — Re-specify the counter-trend guard's window, not its threshold** (highest value).
The guard's failure is structural, not numeric: it watches 180 days while the risk arrives in
weeks. Mechanism: penalize/veto a NEW direction opened against a large **short-window**
realized move, symmetric to `priced_in_check`'s mirror case. This is the already-formulated
"Catalyst-guard" — this run supplies independent confirmation of the class (QCOM again, on
clean data) plus the specification detail that 180d/50% never fires. Deterministic, code
level: #13 proved the prompt version cannot work, because the fundamentals prompt carries
180d technicals only and literally cannot see the recent move.

**P2 — Stop selecting on LLM conviction.** Two clean pieces of evidence now say the gate
input is anti-correlated with outcomes on the tails. Options, in order of conservatism:
(a) re-examine `LT_CONVICTION_GATE=table` — rejected in #6, but that test predates *both* the
fill-convention fix and the PIT fix, so it was scored with the wrong ruler;
(b) gate on the deterministic cell only (shift_score × fundamentals verdict), keeping LLM
conviction as a recorded covariate rather than a selector.
Note P2 interacts with `wf_30x_strong`: the pre-registered hypothesis is that a stronger model
fixes calibration on exactly these three judgement calls. P2(a) and the strong-model split are
competing explanations for the same defect and should not be run confounded.

**P3 — Add a beta-adjusted book to reporting** (measurement, not strategy). Cheap, retroactive,
zero LLM cost; would have surfaced the PEP/KO drag immediately. Same class as the sector-relative
benchmark already in the grader.

**Explicitly not proposed:** tuning `COUNTER_TREND_PCT`/`PRICED_IN_MOVE_PCT` on this window
(threshold-fitting on 30 trades), any prompt sentence (#13), and any change to the direction
mix from n=7 shorts.

## Connection to the tuning loop (Phase 11)

The measurable damage is concentrated in **selection**, not analysis: theses that produce
+1.7% on average get filtered down to a book where the most-selected trades lose 2.5%. If the
outcome-based tuning loop targets one node first, the evidence says it should be the
conviction/synthesis node — that is where the graded signal is strongest and the current
behaviour is demonstrably inverted.
