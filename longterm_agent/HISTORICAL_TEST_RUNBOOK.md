# Long-Term Agent — Historical-Data Test Runbook

**Purpose.** An executable runbook for testing the long-term agent on historical data. This is the
*how-to-run* companion to the strategic [BACKTEST_PLAN.md](BACKTEST_PLAN.md) (which covers the
B0–B4 phasing and the "why"). Read that for the verdict criteria; use this to actually run.

**Status assumed.** B0 (reproducibility: Postgres LLM cache + `function_calling` determinism +
leakage canary) landed in commit `feedfe9`. The no-lookahead engine is built — every reader is
`as_of`-gated, one code path live and in the past.

---

## TL;DR

1. The agent is **architected for backtesting**; the blocker is **data depth**, not code.
2. Dense news only since **2026-02-23** ⇒ today (~2026-06-22) there are **~5 weekly cohorts with
   matured +4w outcomes, all in one regime**. So historical backtesting today is a **harness /
   smoke test**, not a statistical verdict.
3. Run order: **Phase 0 (verify) → 1 (reproducibility) → 2 (run existing harness) → 3 (build B1
   metrics)**. Forward paper-testing (B4) is the honest primary validation path and is already
   accumulating.

---

## The valid testing window (recompute before every run)

Dense news begins **2026-02-23**. For a full 60-day-dense lookback and a matured +4w outcome:

```
full 60d-dense window   ⇒  as_of ≥ 2026-04-24
+4w (28d) outcome matured⇒  as_of ≤ today − 28d   (today 2026-06-22 ⇒ as_of ≤ 2026-05-25)
+2w outcome matured      ⇒  as_of ≤ today − 14d   (⇒ as_of ≤ 2026-06-08)
+12w outcome matured     ⇒  none yet
```

Weekly `as_of` cohorts with matured **+4w**: `2026-04-24, 05-01, 05-08, 05-15, 05-22` → **~5
cohorts × ~30–40 tickers** that clear the `MIN_ARTICLES_FOR_BACKGROUND` gate (mega-cap-tech skew
of the Bloomberg feed). This window grows ~1 cohort/week. **Recompute it each session** — the
numbers above are only valid for 2026-06-22.

---

## Phase 0 — Verify ground truth *(~30 min, no agent runs)*

Don't trust documented measurements; data has accrued. Confirm:

1. **News depth** — articles/day, first dense date, per-ticker 60d counts. Confirms the cohort
   window above and which tickers clear `LT_MIN_ARTICLES` (default 8).
2. **Price bars** — `historical_data_<ticker>` coverage and SPY (needed for excess return). +4w
   grading needs bars out to `as_of + 28d`.
3. **Fundamentals PIT** — EDGAR `filed_date` range (true PIT to 2019) vs yfinance `fetch_date`
   (forward snapshot). Note the asymmetry: a backtest at `as_of` before the first yfinance
   snapshot sees **EDGAR statements only** (no PE/PS/analyst revisions) — the live path is richer
   than the backtest path. This biases any historical vs live comparison.
4. **Tests green:**
   ```bash
   python -m pytest longterm_agent/tests/ -q          # incl. test_leakage_canary
   ```

**Acceptance:** a one-liner — "N valid cohorts × M tickers, +4w matured" — that gates expectations.

---

## Phase 1 — Reproducibility smoke test *(~1 hr; warm run ~free)*

Prove B0 end-to-end before drawing any numbers. Run one cohort twice in one process:

```bash
LT_LLM_CACHE=true LT_MAX_REFLECTION=0 \
  python -m longterm_agent.full_signal_backtest \
    --tickers NVDA --start 2026-06-21 --end 2026-06-21
```

Expect: run 1 cold (records each unique LLM call), run 2 warm (replays) ~8× faster, and the
**signal fingerprint byte-identical** (the recorded acceptance was `b272cf658daa5a1c`, ~258s →
~29s). The decision spine (`shift_score`, z-scores) is rule-based and already deterministic; the
cache makes the LLM **synthesis conviction** reproducible too.

**Acceptance:** identical fingerprint across runs. **If it diverges, STOP** — every later backtest
number is untrustworthy until fixed. (Most likely culprit if it regresses: a non-byte-stable
prompt — corpus must stay sorted by `(published, id)`; see `news_corpus_tool.py`.)

---

## Phase 2 — Run the existing harness on the valid window *(~half day)*

This is "testing on historical data" with what's already built. Always set
`LT_LLM_CACHE=true LT_MAX_REFLECTION=0` and print the provenance stamp.

**A. Full-signal backtest** (whole pipeline → signal → graded vs SPY):
```bash
LT_LLM_CACHE=true LT_MAX_REFLECTION=0 \
  python -m longterm_agent.full_signal_backtest \
    --tickers NVDA AAPL MSFT TSLA AMD JPM AMZN META \
    --start 2026-04-24 --end 2026-05-22 --step 7 --horizon 28
```
Captures: runs / no_data / direction mix / traded (passed risk gate) / win-rate / mean+median
**strategy** excess vs SPY / LONG vs SHORT split. (Strategy excess = +excess for LONG, −excess
for SHORT; only risk-gate-PASS signals count as trades.)

**B. Detection-only report** (do shifts predict the sign of forward excess?):
```bash
# optional: rebuild/extend the graded shift_library first
LT_LLM_CACHE=true python -m longterm_agent.backfill_shift_library --start 2026-04-24 --end 2026-05-22
python -m longterm_agent.grade_shifts          # fill realized +2/4/12w + SPY benchmark
python -m longterm_agent.backtest_report       # hit-rate by shift direction
```

**Acceptance (harness, NOT strategy):** the pipeline discriminates LONG/SHORT/NEUTRAL, the risk
gate filters some fraction (the recorded Phase-5 run filtered 7/24), and **every printout shows
`n` and the single-regime caveat**. The recorded baseline was *24 runs, 17 traded, 41% win,
+0.22% mean / −1.18% median 4w excess* — explicitly n=17 single-rally noise. **Do not report this
as edge.**

---

## Phase 3 — Build B1: walk-forward driver + real metrics *(the main new work)*

Today's `full_signal_backtest.py` is mean-only and grades inline; `backtest_report.py` is
hit-rate only. The gap to a *verdict-grade* harness:

- **`backtest.py`** — one expanding-window walk-forward driver: configurable universe / cohort
  step / horizons (+2/+4/+12w), writing to a **separate `backtest_runs` table** namespaced by a
  `backtest_id`, so backtests never pollute live `longterm_agent_results` or forward paper stats.
- **Extend `backtest_report.py`** with:
  - hit-rate **by conviction bucket** → a **calibration / reliability curve** (directly tests the
    "no-shift conviction is unstable" concern — is conviction predictive?);
  - excess-return **distribution** (mean / median / IQR), not just the mean;
  - **bootstrap confidence intervals**, prominent **n**, and a **min-n gate** before any verdict;
  - information ratio / Sharpe of the signal portfolio; max drawdown; turnover / exposure;
  - splits by **direction**, **shift vs no-shift**, and **regime**.

**Acceptance:** a single command produces a versioned report with CIs; `n` and regime caveats are
unmissable.

---

## Phase 4 — Honest limits & the forward path *(ongoing; primary validation)*

Historical depth is **capped at ~5–8 cohorts of one regime** until a **news archive (B2)** lands —
no metric cleverness fixes that. So the statistically honest validation is **forward
paper-testing (B4)**, already running via the Phase-6 weekly batch:

```bash
python -m longterm_agent.batch_run_longterm --as-of $(date +%F)   # weekly signals → longterm_agent_results
python -m longterm_agent.grade_results                            # fill +4w vs SPY as cohorts mature
```

Formalize: an **equity curve / cumulative-excess** view + cohort-maturity tracker. Timeline —
first real +4w numbers ~1 month out (~4 fresh cohorts); meaningful +4w + first +12w ~1 quarter;
conviction **calibration** realistically needs a full quarter. Defer the expensive **B2** (news
archive import; reconstruct trailing PE/PB from EDGAR equity × price to kill the fundamentals
asymmetry) and **B3** (formal OOS walk-forward protocol) until B1 + forward data justify them.

---

## Exit criteria — when a backtest number is a *verdict*, not a smoke test

(from [BACKTEST_PLAN.md](BACKTEST_PLAN.md) §6 — all must hold)

1. Re-running a cohort reproduces the trade set. — **✅ B0 done; re-verify in Phase 1.**
2. ≥ ~150–300 graded trades spanning **≥ 2 regimes**. — needs B2 + B4.
3. Conviction calibration curve is monotonic. — needs B1 report + B3.
4. Excess return positive **net of turnover**, bootstrap CI excluding 0. — needs B1.
5. Backtest and live fundamentals paths are symmetric. — needs B2.

Until all hold, **every number ships with its `n` and a "single-regime / smoke-test" caveat.**

---

## Standing rules

- Always run backtests with `LT_LLM_CACHE=true` and `LT_MAX_REFLECTION=0`, and print
  `LongTermAgentConfig.provenance()` so runs are comparable (git rev, model, temp, lookback).
- Grading (`grade_*`) is the **only** place allowed to read data dated `> as_of` (outcome, not
  decision). Everything else is `≤ as_of` — enforced by `tests/test_leakage_canary.py`.
- Never tune config on the test window (out-of-sample discipline).
- Recompute the valid cohort window every session; it shifts forward ~1 cohort/week.
