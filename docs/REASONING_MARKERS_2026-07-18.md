# Reasoning-vs-outcome: lexical markers in the 46 graded baseline trades

**Exploratory (hypothesis-generating) — NOT evidence.** Dataset: `wf_30x_f12`, 4w hold
book, thesis + supporting_factors text from `backtest_runs.signal`. n=46, one regime,
marker groups overlap; everything below is subject to the anti-overfit protocol
(burned-grid rule: these were found on the 04-24→06-19 window, so any feature built
from them can only be *rejected* there — acceptance needs post-07-18 cohorts).

| Marker group | hit | rest | delta |
|---|---|---|---|
| hedged ("however/mixed/uncertain/caution") | n=17 · 65% · **+2.57%** | 52% · −0.66% | **+3.2pp** |
| momentum/confirms ("accelerating/confirms/tailwind") | n=42 · +1.60% | n=4 · **−10.69%** | absence is toxic |
| contrarian ("overreaction/pullback/dip/entry point") | n=25 · +1.16% | +(−0.22%) | asymmetric (below) |
| drama/certainty ("classic/decisive/structural/compelling") | n=35 · +0.37% | +1.06% | mild negative |
| thesis length | mid tercile 73% · +4.74% | short 47% · −3.32% | non-monotone |

Splits:
- **Contrarian asymmetry**: contrarian LONGs (n=21 — the agent's favourite trade!)
  48% win / +0.33% vs plain LONGs +2.95%; contrarian SHORTs n=4, 100% win / +5.55%.
  Buy-the-dip is the agent's most common and least profitable habit; fade-the-rally,
  rare and effective.
- **Risk-gate penalty as tell**: trades that reached the book only after a ≥0.05
  conviction penalty: n=4, 25% win, −1.38% (but the one winner is AMD +20.3 — mixed).
- Worst-6 trades: 4/6 carry drama-language; QCOM −36.8% has *only* drama markers and
  zero confirm-language.

## Candidate hypotheses (each needs its own pre-registration before any sweep)

1. **H-confirm-veto (deterministic, code)**: a directional signal whose fundamentals
   sub-verdicts contain no `improving` AND whose thesis carries no confirm-markers is
   capped below the trade gate. Targets the QCOM/no-confirmation class. Text check
   lives in risk_gate (code, not prompt — the B.2 lesson).
2. **H-dip-bar (deterministic)**: contrarian LONG (overreaction cell) requires
   growth_sub or profitability_sub == improving, else −0.1 conviction. Targets the
   21-trade dip-buying habit.
3. **H-hedged-calibration (measurement only, free)**: track hedged-share and
   drama-share per live batch as calibration telemetry; also the mechanism-level
   metric for the queued strong-model A/B (does a stronger judge hedge more where
   it should?). No behavior change — just logged.

Analysis script inline in session 2026-07-18; texts come from the LLM cache /
backtest_runs and are reproducible.
