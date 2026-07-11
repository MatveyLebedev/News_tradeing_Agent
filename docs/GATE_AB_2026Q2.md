# Conviction-Gate A/B — deterministic table vs LLM (Phase 7)

**Date:** 2026-07-11 · **Grid:** 30 tickers × 9 cohorts (2026-04-24…06-19), detection v2,
SPY, frozen config per run · **Runs:** `wf_30x_v0.2` (llm), `wf_30x_v0.2_ct` (llm+guard),
`wf_30x_v0.2_g2` (table), `wf_30x_v0.2_g2ct` (table+guard)

> ⚠ **In-sample, single bull regime.** The counter-trend guard and the table values were
> *motivated* by this very window's failures, so these numbers cannot *validate* them —
> they can only reject or fail-to-reject. The switching decision below is deliberately
> conservative because of exactly this.

## Setup

Identical signals in all four runs (B0 cache replay; detection v2, same synthesis output).
The only difference is what the 0.55 threshold gates on:

- **llm** — the synthesis model's self-reported conviction, minus risk penalties (live behavior).
- **table** — deterministic `CONVICTION_TABLE[fundamentals verdict][shift_score bucket]`
  (+ advisory nudge, live only), minus the same penalties; LLM value recorded as
  `signal.conviction_raw`.
- **counter-trend guard** (both modes) — −0.2 conviction when the signal fights a ≥50%
  trailing 180d move (the priced-in guard can't see this case: it only fires when price
  moved WITH the signal).

## Results (4w / 28d horizon; graded trades)

| gate | n | win | mean | median | worst | sum |
|---|---|---|---|---|---|---|
| llm (baseline) | 44 | 55% | −0.54% | +1.05% | −40.8% | −23.9% |
| llm + guard | 45 | 53% | −0.71% | +1.01% | −40.8% | −31.9% |
| table | 22 | 50% | **−2.89%** | +0.01% | −40.8% | **−63.6%** |
| table + guard | 20 | 55% | −0.63% | +0.70% | −36.8% | −12.7% |

2w: table+guard is the only variant with a positive mean (+1.14%, win 60%, worst −11.3%);
all others negative.

## The two blowups, traced

| trade | llm | llm+guard | table | table+guard |
|---|---|---|---|---|
| INTC 04-24 SHORT (−40.8%) | trades @0.75 | **trades @0.55 — knife-edge** | trades @0.70 | **FILTERED @0.50** ✓ |
| QCOM 05-01 SHORT (−36.8%) | trades @0.78 | trades (no trailing rally → guard correctly silent) | trades @0.70 | trades — **irreducible by these gates** |

Two lessons: (1) under the llm gate a flat −0.2 penalty is **toothless exactly where it
matters** — LLM overconfidence (0.75+) eats it and lands ≥ threshold; (2) QCOM was a
by-the-book confirms-cell SHORT that got squeezed by a new catalyst — market risk, not a
gate defect.

## Why the table gate FAILED selection (the decisive cut)

Of the llm baseline's 44 trades, the table **kept 22 (excess sum −63.6%) and dropped 24
(sum +39.7%)** — it kept the losers and dropped the winners. Direction mix explains it:

- kept: 12 SHORTs (−48.0%) + 10 LONGs (−15.6%) — "confirms" cells in this window were
  mostly negative-shift SHORTs into a bull market;
- dropped: 20 LONGs (**+38.0%**) + 2 SHORTs (+1.7%) — the LLM's profitable trades were
  largely *contradicts*-cell contrarian LONGs (LLY, C, AAPL — overreaction plays) that
  the conservative matrix devalues by construction.

This is regime-dependent (in a bear window confirms-SHORTs may flip to winners), and one
regime cannot settle table weights. But the burden of proof for switching is not met.

## Decision (2026-07-11)

1. **Gate stays `llm`** (default unchanged). The deterministic 3×4 table is too coarse to
   replace LLM trade selection: it inverted selection quality on the only window we have.
2. **Counter-trend guard stays ON** (`LT_COUNTER_TREND_PENALTY=0.2`): under the llm gate
   it removed zero trades on this window (cost-free) while protecting the mid-conviction
   range [0.55, 0.75); its knife-edge limitation at high conviction is documented above.
   Raising the penalty to clear 0.75+ would be tuning on this window — not done.
3. **Both conviction values now persist** (`signal.conviction_raw` = LLM,
   `signal.conviction_gate` = gated value, `signal.gate` = mode; scalar `conviction`
   column = the gating value). This is the dataset the Phase 7 "later" step needs:
   isotonic calibration per DETECTION_VERSION once ~80+ graded v2 signals accumulate
   live — calibrate the number, don't replace the selector.
4. Re-examine after forward accumulation spans a second regime.

## Reproduce

```bash
LT_METRICS_VERSION=2 LT_CONVICTION_GATE=table LT_LLM_CACHE=true LT_MAX_REFLECTION=0 \
LT_DEEP_ANALYSIS=false python -m longterm_agent.backtest --backtest-id <id> \
  --tickers <30 names> --start 2026-04-24 --end 2026-06-19 --step 7 \
  --horizons 14 28 84 --benchmark SPY --workers 20 --asof-today 2026-07-10
```

Known harness caveat observed during the A/B: a transient LLM/API failure during a sweep
falls back to NEUTRAL and is NOT cached, so a later identical run can differ on that one
cohort (GOOGL 05-08 traded in `_ct` but not in the baseline). Transient DB/Neo4j outages
mid-sweep error individual cohorts; the resume path (`--backtest-id` re-run) completes
them idempotently.
