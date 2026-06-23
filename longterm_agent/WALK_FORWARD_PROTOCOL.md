# Walk-Forward Validation Protocol (Backtest Plan B3)

The documented, repeatable procedure for evaluating the long-term agent so a result is
*defensible* — out-of-sample, corrected for multiple testing, and reported with confidence
intervals — rather than an overfit, single-regime smoke test. The mechanics (running the
pipeline across cohorts, grading vs a benchmark) live in `backtest.py` (B1) and `forward_report.py`
(B4); this doc is the **discipline** layer that governs how they are used and read.

> A backtest number is only a *verdict* when this protocol was followed. Otherwise it ships with
> its `n` and a "single-regime / smoke-test" caveat — which the report prints automatically.

---

## 1. Design

- **Expanding-window walk-forward.** Cohorts advance forward in time; each `(ticker, as_of)`
  decision uses only data `≤ as_of` (enforced by the PIT readers + the B0 leakage canary). No
  fold ever sees its own future.
- **Weekly cohorts** (`--step 7`) across the configured universe.
- **Horizons** `+2w / +4w / +12w` (`--horizons 14 28 84`): a row per horizon, each graded
  independently, so a short-horizon edge isn't conflated with a long-horizon one.
- **Benchmarks: SPY *and* a sector ETF.** "Beat the market" must not just mean "tech rose."
  Run the sweep once per benchmark (`--benchmark SPY`, then `--benchmark XLK`, …) under the same
  `--backtest-id` family. *Data note:* only SPY is currently in `stock_prices_daily`; the
  sector-ETF leg is blocked until those prices are ingested.

## 2. Out-of-sample discipline (the anti-overfitting core)

- **Freeze the config on the test window.** Models, thresholds, prompts, and reflection settings
  are fixed *before* the sweep. No tuning, no peeking, no "try again with a different threshold"
  on the same window.
- **One config = one `backtest_id`.** Each row stamps `config.provenance()` (git rev, models,
  temperature, reflection, cache, lookback). The report **fails the OOS check** if a `backtest_id`
  contains more than one provenance stamp — that means knobs changed mid-sweep, so the result is
  not out-of-sample. Changed the config? Use a fresh `--backtest-id` and compare across ids.
- **Reproducibility (B0).** Run with `LT_LLM_CACHE=true LT_MAX_REFLECTION=0` so the sweep is
  byte-reproducible and a re-run/A-B is cheap.

## 3. Multiple-testing awareness

Trying many tickers (and many configs) inflates the chance of a spurious "winner." The report
runs a per-ticker test (H0: mean excess ≤ 0) via a one-sided bootstrap p-value, then applies
**Benjamini–Hochberg FDR control at 5%** across the tickers tested (those with ≥3 graded trades).
Report how many survive — not how many had a positive mean. Across configs, treat each
`backtest_id` as an additional test and discount accordingly.

## 4. Calibration gate (before conviction is a sizing input)

The conviction reliability curve (win-rate by conviction bucket) must be **monotonically
increasing** before conviction is trusted to size positions. The report prints
`calibration monotonic (sizing-trustworthy): YES/NO`. Until YES on a non-trivial `n`, treat all
trades as equal-weight — do not scale by conviction.

## 5. Reporting

Every run reports, per horizon: win-rate and mean excess **with bootstrap CIs**, the return
distribution (median/IQR/min/max), Sharpe/IR and max drawdown of the per-cohort equity curve,
and splits by **direction**, **shift vs no-shift**, and **regime** (SPY trailing return). `n` and
the min-n gate (n<30 ⇒ ESTIMATE) are unmissable. The forward report (B4) adds the live equity
curve + cohort-maturity tracker.

## 6. Procedure (repeatable)

```bash
# 1. freeze config; pick a fresh id for this config
LT_LLM_CACHE=true LT_MAX_REFLECTION=0 python -m longterm_agent.backtest \
    --backtest-id wf_2026q2_v0.1 \
    --tickers NVDA AAPL MSFT JPM TSLA AMZN META \
    --start 2026-03-01 --end 2026-06-01 --step 7 --horizons 14 28 84 --benchmark SPY
# 2. (when sector prices exist) repeat vs the sector ETF
#    ... --backtest-id wf_2026q2_v0.1_xlk --benchmark XLK
# 3. read the report: OOS check ✓, FDR survivors, calibration gate, CIs, per-regime
python -m longterm_agent.backtest --backtest-id wf_2026q2_v0.1 --report-only
```

## 7. Acceptance — a result is reportable as a *verdict* only when ALL hold

1. Re-running the cohort reproduces the trade set (B0).
2. ≥ ~150–300 graded trades spanning **≥ 2 regimes** (needs B2 data depth + B4 forward time).
3. Conviction calibration curve is **monotonic** (§4).
4. Excess return positive **net of turnover** with a bootstrap CI excluding 0, surviving FDR (§3).
5. Backtest and live fundamentals paths are symmetric (B2 valuation PIT — done).
6. The OOS check passes (single frozen config per `backtest_id`, §2).

Until then, results are **estimates / harness checks**, reported with `n` and regime caveats —
and the statistically honest path remains **forward accumulation (B4)**, which is running from
today.
