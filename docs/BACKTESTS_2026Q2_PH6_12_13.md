# Queued backtests — Phase 6-pre / 12 / 13 (wf_30x grid, 2026-07-12..15)

Three A/Bs from MODIFICATION_PLAN.md "Queued backtests", run sequentially on one healthy
OpenRouter key from a git worktree pinned @ `82f1ff8` (Phase 13 commit; single provenance
stamp per id — OOS discipline holds on every run).

**Grid** (identical across ids): 30 tickers (the wf_30x_v0.2 set), 9 weekly cohorts
2026-04-24 → 2026-06-19, horizons 2w/4w/12w, benchmark SPY, `--asof-today 2026-07-10`,
LLM cache ON, reflection 0, 20 workers. 12w horizons are unmatured — 2w/4w reported.

**Caveat that applies to every number below:** all trades live in ONE regime (SPY bull,
VIX<20 / HY<p50 for ~95% of graded rows — now visible directly in the Phase 14 regime
splits). Bootstrap CIs on mean excess include zero everywhere; treat deltas as
directional evidence, not proof. 12w columns fill as horizons mature (grade re-runs are
idempotent).

## Ladder at a glance (4w horizon, hold book)

| id | change vs previous rung | traded→graded | win | mean excess | Sharpe | worst | stopped mean |
|---|---|---|---|---|---|---|---|
| `wf_30x_v0.2` | baseline (detection v2) | 46→44 | 55% | −0.54% | 0.57 | −40.8% | +0.27% |
| `wf_30x_f12` | + Phase 12 fundamentals v2 | 49→46 | 57% | **+0.53%** | 0.91 | −36.8% | +0.71% |
| `wf_30x_v0.2_deep` | + deep analysis v1 ON | 42→39 | 62% | **+1.78%** | 1.30 | −36.8% | +2.04% |
| `wf_30x_f12_v3` | detection v3 (sector-relative) | *(pending)* | | | | | |

## 1. Phase 12 — fundamentals v2 wiring (`wf_30x_f12` vs `wf_30x_v0.2`)

Same detection (0.2.0), same gates; only the fundamentals node's evidence changed
(STORED Table-B fields wired: cash-flow, balance, opex, EPS path, street, peer P/E;
5 sub-verdicts).

| horizon | metric | v0.2 | f12 | Δ |
|---|---|---|---|---|
| 2w | mean excess | −0.88% | +0.62% | **+1.50pp** |
| 2w | Sharpe | 0.09 | 0.59 | +0.50 |
| 2w | worst trade | −48.0% | −12.9% | +35.1pp |
| 4w | mean excess | −0.54% | +0.53% | **+1.07pp** |
| 4w | Sharpe | 0.57 | 0.91 | +0.34 |
| 4w | win rate | 55% | 57% | +2pp |

The richer filed picture changed the traded set (46→49) and cut the left tail hard at
2w. 4w calibration is still non-monotonic (the 0.55-0.65 conviction band remains toxic:
mean −4.61%) — conviction is a gate, not a sizing input, unchanged conclusion.

**Verdict: keep fundamentals v2** (it already shipped at `1162a76`; this A/B validates
the wiring — the diff vs v0.2 isolates exactly the Phase 12 change).

## 2. Phase 6-pre — deep analysis measured (`wf_30x_v0.2_deep` vs `wf_30x_f12`)

First-ever measurement of the deep-analysis advisory nudge (every prior backtest ran
deep OFF while live ran deep ON). Both ids run from the SAME tree with Phase 12 in — the
diff isolates `LT_DEEP_ANALYSIS=true` (the plan's "accept the confound" option resolved
by making f12 the baseline).

| 4w | f12 (no deep) | deep v1 | Δ |
|---|---|---|---|
| traded→graded | 49→46 | 42→39 | −7 trades |
| win rate | 57% | 62% | +5pp |
| mean excess | +0.53% | +1.78% | **+1.25pp** |
| Sharpe | 0.91 | 1.30 | +0.39 |
| stopped book mean | +0.71% | +2.04% | +1.33pp |
| max drawdown (per-cohort curve) | −3.23% | −2.01% | better |
| calibration monotone? | NO | **YES** | fixed |

2w points the same way (+0.62%→+1.39%, Sharpe 0.59→0.78). The ±0.2 advisory nudge
dropped 7 trades and the surviving book improved on every aggregate — the nudge is
filtering bad trades, not adding noise.

**Verdict (6-pre decision): (a) — the nudge HELPS.** Live `LT_DEEP_ANALYSIS=true` is
validated (the live/backtest asymmetry is now measured, in deep's favor); Phase 6
(deep v2: planner + parallel cited threads, shipped `c603f34` behind `LT_DEEP_V2=false`)
is justified, and queued backtest #4 (`wf_30x_f12_deepv2`) is worth its cost.
Decision rule stands: deep v2 must beat BOTH f12 and deep v1 to earn the flag ON.

## 3. Phase 13 — sector-relative detection v3 (`wf_30x_f12_v3` vs `wf_30x_f12`)

*(pending — compare v2-vs-v3 backfill + grade + backtest in flight)*

## Protocol notes

- Deep A/B first attempt (2026-07-12) was destroyed by the OpenRouter weekly key limit
  mid-sweep (403 → fallback-NEUTRAL contamination; 621 rows deleted). This re-run
  completed with **zero** 403/key-limit markers under a per-step log guard.
- `wf_30x_f12` / `wf_30x_v0.2_deep`: 810 rows each (270 pipelines × 3 horizons),
  graded through 2026-07-10; `regime_context` stamped on all rows at grade time
  (Phase 14), enabling the vix/hy splits shown in reports.
- Multiple-testing (BH FDR 5%, per-ticker H0 mean excess ≤ 0): CRM survives everywhere;
  CRM+GS+LLY at 4w on v0.2, f12 and deep — stable across variants, which is what a real
  (if narrow) edge looks like under correction.
