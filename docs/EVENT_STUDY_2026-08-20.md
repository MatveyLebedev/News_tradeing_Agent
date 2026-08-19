# Event study — price around detected shifts, the fundamentals verdict, and the `confirms` filter (2026-08-20)

**Status: EXPLORATORY — hypothesis-generating, NOT evidence.** Every split in this document
was chosen *after* seeing outcomes, on windows that are burned. Under the anti-overfit
protocol (`BACKTEST_LEDGER.md` §1) nothing here can accept a feature. It can motivate a
pre-registration, sharpen one, or reject one — nothing more. Numbers are recorded so they
are not silently re-derived later.

Companion to the pre-registration it informed: `AB_PLAN_matrix_cell.md` (`confirms_only`).

---

## 0. Two measurement errors found and fixed mid-analysis — read this first

**0.1 `excess` is direction-signed.** `stats.trade_excess()` returns `-(fwd - bench)` for a
SHORT, i.e. the column is the leg's **P&L**, not the price move. The first pass through this
analysis flipped it a second time. `beta.beta_adjusted_excess()` signs the same way.

Everything the first pass said about the short leg was inverted and is void. The corrected
reading is in §6. The sentinel is already in the codebase — `stats.unrealized_excess`'s
docstring says "flipping twice is how a SHORT's excess quietly goes wrong."

**0.2 The pre-move was read off the day-0-normalised path.** `-path[0]` measures a trailing
return from the *ending* price (`R/(1+R)`), not the standard `R`. Checked: **0 bucket flips
in 1188 decisions**, rank correlation 0.9985 — so every split in §5 is unaffected. Only the
printed magnitudes were wrong, and asymmetrically: rises understated (mean |pre-move| 8.93%
vs 10.27% correct), falls essentially unchanged. §5 carries corrected magnitudes.

Event-study *paths* were computed from prices directly and are unaffected by either error.

---

## 1. Data and method

| | |
|---|---|
| detections | `shift_library`, 323 with a direction, 2026-04-30 → 2026-08-09, 18 cohorts, 59 tickers |
| decisions | `backtest_runs`, 28d horizon, traded, `gate_trace ? 'table_inputs'` (P2a, 2026-07-20 onward) |
| verdict | `gate_trace->'table_inputs'->>'verdict'` |
| prices | `stock_prices_daily` (132 tickers + SPY, 2024-06-17 → 2026-08-07) |
| live | `longterm_agent_results`, 341 runs, 9 dates, 64 tickers, 2026-06-15 → 2026-08-17 |

**Dedup.** Sweeps re-run the same grid under different configs, so rows are collapsed to one
per `(ticker, as_of)`, keeping only pairs whose verdict AND direction are unambiguous across
sweeps. **63 of 567 shift-bearing pairs (11%) carry different verdicts across configs** and
are dropped — recorded as a finding in its own right: the verdict is not config-stable.

**Day 0** = the last close at or before `as_of`. The backward leg uses only closes ≤ `as_of`
and is PIT-clean; the forward leg sees the future by construction, which `PIT_RULES.md` §4
permits for grading-side code.

**CIs** are 95% percentile bootstraps resampling **cohorts** (`as_of`), not rows, because
detections cluster on dates. Cells with n<20 or <3 cohorts are reported but marked thin.

**Caveat that binds everything:** one regime. SPY +15.6% over the window; the deepest 4-week
SPY drawdown anywhere in the sample is **−2.35%**. No observation of a real market decline
exists in this data.

---

## 2. Price around a detected shift, by shift sign

Cumulative return minus SPY, normalised to 0 at day 0.

| | day −21 | day +10 | day +28 | median +28 |
|---|---|---|---|---|
| positive (n=122, 11 coh) | −3.88 **[−6.88, −1.13]** | +1.09 [+0.06, +2.21] | +0.93 [−1.36, +2.49] | −3.45 |
| negative (n=126, 12 coh) | +0.49 [−1.92, +1.93] | +1.67 **[+0.51, +2.87]** | +2.00 [−1.03, +4.08] | −0.95 |

Two claims survive their CIs:

* **A positive shift is detected after the stock has already run.** Day −21 excludes zero.
* **After a negative detection the stock rises, significantly at 5–10 sessions.** The move
  the shift describes has already happened; a short entered on it is late.

Nothing else in this cut survives. Pooled direction-signed 4w excess over all 248 graded
detections: **−0.27%, CI [−1.39, +1.49]**, 47% positive. Bare detection does not predict
direction.

---

## 3. By fundamentals verdict (detected shifts, unsigned price path)

| verdict | n | day −21 | day +28 | median |
|---|---:|---|---|---:|
| `confirms` | 182 | +0.29 [−1.88, +2.73] | **+3.92 [+2.13, +5.73]** | +1.12 |
| `contradicts` | 149 | +1.34 [−0.58, +3.06] | **+5.34 [+2.49, +8.57]** | +0.50 |
| `ignores` | 89 | +2.59 [+0.49, +4.68] | −2.31 [−5.06, +1.17] | −3.31 |

The verdict separates forward paths better than the shift sign does. `ignores` is the only
class that turns down.

---

## 4. By matrix cell (verdict × the decision taken)

| cell | n / coh | into detection | +28d | median | P&L for the side |
|---|---:|---:|---|---:|---:|
| `LONG · confirms` | 72 / 21 | +3.81% | **+8.95 [+4.49, +13.51]** | +4.72 | +8.95 / +4.72 |
| `LONG · contradicts` | 69 / 21 | +0.49% | **+8.20 [+3.71, +14.09]** | +0.85 | +8.20 / +0.85 |
| `LONG · ignores` | 16 / 11 ⚠ | −2.25% | −1.89 [−6.70, +3.91] | −3.10 | −1.89 / −3.10 |
| `SHORT · confirms` | 87 / 23 | −3.82% | −1.25 [−4.97, +2.59] | −1.76 | +1.25 / +1.76 |
| `SHORT · contradicts` | 4 / 4 ⚠⚠ | −0.74% | +11.73 | +11.14 | −11.73 / −11.14 |
| `SHORT · ignores` | 6 / 4 ⚠⚠ | −5.14% | +3.20 | +2.67 | −3.20 / −2.67 |

**The short book is one cell.** 87 of 97 shorts are `confirms`, and it is flat: the name
already fell 3.82% before the detection and adds only 1.25% after.

**The cells the matrix does not license all lose.** `contradicts` permits contrarian LONG or
NEUTRAL, never SHORT; `ignores` has no cell at all. The 26 decisions sitting outside the
matrix are the origin of the `confirms_only` registration.

---

## 5. Split by the pre-move (standard trailing excess, 21 sessions)

| pre | side | verdict | n / coh | pre-move | +28d | median |
|---|---|---|---:|---:|---|---:|
| grew | LONG | `confirms` | 46 / 20 | +15.84 | +8.51 [+2.83, +14.51] | +5.17 |
| grew | LONG | `contradicts` | 34 / 16 | +12.50 | +13.83 [+8.13, +20.91] | +3.94 |
| grew | SHORT | `confirms` | 31 / 15 | +11.81 | −1.04 [−5.59, +3.32] | −0.74 |
| fell | LONG | `confirms` | 26 / 12 | −8.40 | +9.75 [+3.08, +19.59] | +2.30 |
| fell | LONG | `contradicts` | 35 / 16 | −7.44 | +2.74 [−3.77, +13.88] | −1.88 |
| fell | SHORT | `confirms` | 56 / 19 | −10.44 | −1.37 [−5.86, +3.09] | −2.12 |
| fell | NEUTRAL | `contradicts` | 32 / 17 | −8.49 | +0.29 [−2.43, +2.76] | −1.36 |
| fell | NEUTRAL | `ignores` | 36 / 13 | −7.67 | −2.05 [−6.15, +3.33] | −3.60 |

* **`LONG · confirms` is the only cell indifferent to the pre-move** — it works whether the
  stock came in rising or falling, with a positive median in both.
* **`contradicts` works in the wrong half.** The matrix intends it as the overreaction trade
  (negative shift + improving fundamentals → buy the dip); the "fell before" half is exactly
  where it fails (median −1.88, CI covers zero). What works is `contradicts · grew`, which is
  momentum with a disagreeing fundamental, not the intended mechanism.
* **The short leg is indifferent to the pre-move**, flat both ways.

---

## 6. LONG vs SHORT across every swept book (CORRECTED — see §0.1)

Deduped to unique `(ticker, as_of)`; `excess` is each leg's P&L.

| horizon | leg | n | P&L mean | median | CI95 | the names' price vs SPY |
|---|---|---:|---:|---:|---|---:|
| 2w | LONG | 219 | +1.47 | −0.40 | [+0.52, +2.39] | +1.47% |
| 2w | SHORT | 94 | +1.08 | +1.44 | [−0.56, +2.40] | −1.08% |
| 4w | LONG | 285 | +2.65 | **−1.59** | [+0.96, +4.49] | +2.65% |
| 4w | SHORT | 163 | +1.28 | **+1.35** | [−0.69, +3.11] | −1.28% |
| 12w | LONG | 97 | +7.74 | −2.03 | [+2.03, +18.94] | +7.74% |
| 12w | SHORT | 41 | +7.44 | +7.87 | **[+3.46, +11.45]** | −7.44% |

Ranking spread (longed names minus shorted names, in price): **+2.56 / +3.93 / +15.18pp** —
it grows with the horizon and does not invert.

**Both legs make money at every horizon**, and their medians are opposite in sign: the long
leg's median is negative everywhere (its mean is a tail), the short leg's is positive
everywhere. The reliable leg is the short one; the lottery is the long one. Per book, 23 of
27 books have the long leg out-earning the short — but books are re-runs of one grid, so
that count is not 23 independent observations.

---

## 7. What explains it

**7.1 One sector, one month, carries the long leg.** 18 of 285 longs (6%) are April-2026
semiconductors: mean **+34.42%**. Everything else: **+0.50%**, median −2.19%. The top six
long trades are INTC ×3 (+98/+78/+62%), AMD +72%, MU ×2 (+69/+55%).

**7.2 Beta, not selection.** Mean beta: longs 1.06, shorts 0.63.

| | beta<0.8 | 0.8–1.2 | beta>1.2 |
|---|---:|---:|---:|
| LONG | −0.42 (n=63) | +1.41 (n=59) | **+7.78 (n=107)** |
| SHORT | +0.53 (n=70) | +3.84 (n=28) | **−3.75 (n=32)** |

In a +15.6% window the book earns by being long high beta and short low beta. 7.1 and 7.2
are not independent — semis are high beta.

**7.3 Skew is structural.** LONG skew **+2.53** (max +98%); SHORT skew **−0.24** (max +22%).
The long side can produce a +98% name; the short side's universe cannot. The long leg's mean
will always depend on a tail and its median will stay negative.

**7.4 Not concentration.** HHI 0.020 long (55 names), 0.032 short (48 names). Both diversified.

**7.5 The short leg is a nearly 1:1 hedge, undersized.** Unhedged slope vs SPY: LONG **+1.09**
(R²=0.28), SHORT **−0.96** (R²=0.18). Per trade the short cancels the long's market
sensitivity; the book is 1.5:1 long by count, so the net is **+0.62**. In the excess frame
every slope is ~0 (+0.19 / +0.09 / −0.14, R² ≤ 0.02) — the benchmark convention does net the
market out, as designed. Note the short hedges in absolute terms but *underperforms its own
benchmark* in down cohorts (excess −0.74 vs +2.46): as "better than shorting SPY" it does not
work. Both statements are true in different frames; they must not be mixed.

---

## 8. Book variants — what the `confirms` filter would do

4w, trace-stamped sweeps, deduped. A = book as traded. **D = the registered `confirms_only`.**
C = the stricter long-only variant.

| | A: current | **D: `confirms_only`** | C: `LONG·confirms` |
|---|---:|---:|---:|
| trades | 351 | **210 (60%)** | 90 (26%) |
| LONG : SHORT | 1.54 | **0.75** | long-only |
| names | 67 | **62** | 38 |
| sectors | 9 | **8** | 7 |
| HHI ticker | 0.020 | **0.023** | 0.040 |
| HHI sector | 0.162 | **0.155** | 0.212 |
| trades / date | 9.8 | 6.0 | 3.5 |
| active dates | 36/36 | **35/36** | 26/36 |
| dates < 3 trades | 1 | 10 | 23 |
| mean beta | 0.84 | **0.88** | 1.23 |
| top-5 share of P&L | 36.1% | 45.6% | 57.5% |
| **market slope (unhedged)** | +0.62 (R²=.18) | **+0.14 (R²=.01)** | +0.86 (R²=.14) |
| P&L SPY<0 / SPY≥0 | +1.52 / +4.95 | **+2.37 / +4.37** | +6.79 / +11.79 |
| April-semis share | 5.1% | **6.7%** | 15.6% |
| mean ex-semis | +1.17 | **+1.50** | +1.72 |
| pre-May / post-May | +5.92 / **+0.85** | +8.57 / **+0.45** | +10.86 / **+2.37** |
| P&L | +2.99 | +3.77 | +6.99 |
| median | +0.49 | +1.45 | +2.03 |
| CI95 | [+1.18, +4.79] | [+0.78, +6.40] | [+2.28, +11.08] |

**D costs almost nothing measurable in diversification** — five names of 67, one sector
(Utilities, 0.3% of the book), sector concentration actually *improves*. It cuts 40% of
trades and leaves 10 thin cohorts of 36.

**D turns the book market-neutral.** Flipping the balance to 0.75:1 short drops the unhedged
slope from +0.62 to **+0.14** (R²=0.01) while mean position beta barely moves (0.84 → 0.88).
Neither this document's author nor `AB_PLAN_matrix_cell.md` anticipated this; it is the single
most interesting property found, and it is untested outside a bull window.

**C is a different proposition and a worse one.** Concentration doubles, a third of names go,
half the cohorts fall below three trades, the book becomes fully market-exposed at beta 1.23,
and the April-semi share triples to 15.6% — so 86% of its headline gain is one sector in one
month taking a larger share of a smaller book (only +0.55pp of +4.00pp survives removing it).

**Both variants are worse than the incumbent after May.** A +0.85, D +0.45, C +2.37 —
decomposition puts D's drag on `SHORT · confirms` at roughly −0.5% in the later half.

---

## 9. The live book (2026-06-15 → 2026-08-17)

341 runs, 9 dates, 64 tickers. Outcomes: NEUTRAL 275 (81%), LONG 27, SHORT 20, NO_DATA 19.
**34 traded** (10% of runs), zero synthesis failures. Balance 18:16 — far more even than the
sweeps' 1.5:1.

**Maturity is the binding constraint: 18 of 34 have a 2w outcome, 7 have 4w, 0 have 12w.**

| book | n | mean | median | wins |
|---|---:|---:|---:|---:|
| 2w | 18 | +0.88% | +0.28% | 9/18 |
| 4w | 7 | −0.58% | −1.85% | 2/7 |

Six of the seven matured trades are one cohort (2026-07-19), and one trade carries the book:
NKE SHORT +14.26%. Without it the other six average −3.06%. Two stops fired (NFLX short,
AMD long).

Counterfactual filters on the live book:

| | kept | names | 2w | 4w |
|---|---:|---:|---|---|
| as traded | 34 (18L/16S) | 25 | +0.88% (n=18) | −0.58% (n=7) |
| `confirms_only` | 23 (68%, 9L/14S) | 19 | −0.21% (n=13) | +0.67% (n=5) |
| `LONG · confirms` | 9 (26%, 9L/0S) | 8 | −2.43% (n=5) | +0.68% (**n=1**) |

The stopped book (Phase 5.5 exit discipline) tracks the horizon book closely: −1.44% as
traded, +0.60% under `confirms_only`, on the same 7 and 5 rows.

**The live data cannot distinguish anything** — the `LONG·confirms` 4w book is one trade,
and the two horizons disagree in sign for both filters. The disagreement has a single
identifiable cause: the rule removes the book's **best 2w trade** (NOW 07-26,
`LONG · ignores`, +21.33% at 2w, not yet matured at 4w) and its **worst 4w trade**
(AMD 07-19, `LONG · contradicts`, −5.56%, stopped out). Nothing deeper is being measured.

The registered rule would remove **11 of 34 live trades (32%)**: eight `LONG · contradicts`,
one `SHORT · contradicts` (PFE 08-09), and two `ignores` (NOW 07-26 long, SBUX 08-17 short).
Of the five removed trades with a 2w outcome the mean is **+3.71%** — driven entirely by NOW.
Of the two with a 4w outcome both are losers.

The live book is far more even than the sweeps: 18 LONG / 16 SHORT, and the filter takes it
to 9L/14S — the same flip to short-heavy seen in §8.

*Correction (recorded so it is not re-derived):* an earlier pass read PG 08-02 and QCOM
08-09 as trading with no fundamentals verdict. Wrong column — both carry `verdict=confirms`;
what is null on those two rows is `background_shift->>'direction'` (`has_shifted` is true).
**Zero of the 34 traded live rows lack a verdict.** The `unknown` hole documented in
`AB_PLAN_matrix_cell.md` is real in the sweeps (25 traded rows) but has not yet appeared live.
The null shift direction on those two rows is a separate, unexplained oddity worth its own
look.

At 34 trades in 9 weeks and 32% of them in the affected class, the live path accrues about
**1.2 relevant decisions per week**.

---

## 10. What this licenses, and what it does not

**Does not license:** accepting `confirms_only`, re-opening `contradicts`, adopting `C`,
or putting any number from this document into an acceptance criterion. Every split is
post-hoc on burned windows; the +8.95%, +3.77% and +6.99% figures in particular must never
become targets.

**Does license, because it is mechanism-level and deterministic:**

1. Adding to `AB_PLAN_matrix_cell.md` stage-1 **reported** set (not criteria): the LONG:SHORT
   balance of the resulting book, the unhedged slope vs SPY, the split of removed longs by
   pre-move sign and by verdict, and the config-instability share of the verdict (§1).
2. Treating the `unknown`/missing-verdict hole as in-scope for the registered rule — it is
   real in the sweeps (25 traded rows); §9 records that it has NOT yet appeared live.
3. Recording §7.5 (the short is a ~1:1 but undersized hedge) as the reason a long-only variant
   is a materially different risk proposition, not a stricter version of the same one.

**Open questions worth their own pre-registration, none of them started here:**

* Does the `LONG · confirms` cell's indifference to the pre-move (§5) hold out of sample?
* Is `contradicts`'s failure confined to its intended half (§5), and does that survive?
* Does D's market-neutrality (§8) hold when SPY actually falls? Untestable on this data.

---

## 11. Reproduction

Every figure in this document comes from `longterm_agent/event_study.py`, which carries the
two sign conventions of §0 in code and in its module docstring:

```bash
python3 -m longterm_agent.event_study --cut all --plot --outdir /tmp/es
```

Individual cuts: `direction` (§2), `verdict` (§3), `cell` (§4), `premove` (§5), `legs` (§6),
`variants` (§7–8), `spy` (§8), `live` (§9). Sources: `backtest_runs`, `shift_library`,
`stock_prices_daily`, `longterm_agent_results`, and the `fundamentals` yfinance `info`
sector field.
