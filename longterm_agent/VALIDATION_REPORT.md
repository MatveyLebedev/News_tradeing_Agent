# Long-Term Agent — Validation Report (T0–T2)

**Date:** 2026-07-01  ·  **Backtest ID:** `wf_2026q2_v0.1`  ·  **Agent version:** 0.1.0  ·  **git:** `bf68a0d`

Validation of the long-term trading agent on the **existing (single-regime) data**, run locally against
the live DB before any deploy. Scope: does the agent *work* end-to-end (machinery), not does the
*strategy* work (that needs multi-regime forward accumulation).

---

## TL;DR — Verdict

- ✅ **Machinery validated end-to-end.** 74/74 unit tests, reproducible (byte-identical warm re-run),
  no-lookahead intact, and the walk-forward harness (`backtest.py`, never run before) executed all
  72 cohorts → graded → produced the B1/B3 report with OOS / FDR / calibration gates.
- ❌ **Strategy edge NOT demonstrated.** Numbers are `ESTIMATE` only: n<30, a single bull regime,
  the excess is concentrated in a few names, conviction is not calibrated, and the outcome grading is
  contaminated by **stale price data** (see §6).
- 🟡 **Recommendation:** green-light **deploy** — but only to start accumulating **multi-regime forward
  evidence** (the honest validation path). First **restore the data pipeline** (daily prices +
  fundamentals scheduling, §6). Treat every number here as a harness check, not a verdict.

---

## 1. Test environment (infra prerequisites that had to be fixed first)

Running the agent locally required unblocking data access that the ISP had cut off:

| Item | Finding | Resolution |
|---|---|---|
| Postgres | 5432 blocked by ISP (app-layer packets dropped) | external port **5544** (docker `5544→5432`); local `.env` `DB_PORT=5544` |
| Neo4j | 7687 blocked by ISP | prod container recreated adding **7688→7687** (same `neo4j_data` volume, no data loss); `.env` `NEO4J_URI=…:7688` |
| LangSmith tracing | API key returned 403 (dead) | key rotated by user; ingest verified (200); `--trace` made default-on in `run_longterm_agent` |

Server side: only `bloomberg_news_fetcher` + infra (postgres, neo4j, telegram-bot) are running. The
`longterm_agent` scheduler, daily-price, and fundamentals jobs are **not deployed** (see §6).

---

## 2. T0 — Unit tests & reproducibility ✅

- **`pytest longterm_agent/tests/` → 74/74 pass** (incl. leakage canary, EDGAR PIT normalization,
  shift metrics, risk-gate guard, shift-library leakage/version filters, walk-forward stats helpers).
- **Reproducibility (B0):** NVDA `as_of=2026-06-22` cold run vs warm re-run → signal + full LLM thesis
  **byte-identical**; warm run 42s vs ~240s cold (LLM cache replays). Only diff: a benign
  `LangChainBetaWarning`.

## 3. T1 — Behavior smoke (multi-ticker) ✅

Full pipeline run end-to-end for **NVDA** (tech) and **JPM** (bank); coherent output, no crashes,
no-lookahead respected, sector-aware fundamentals (JPM `gross_margin=0.0` handled — banks don't file it;
PIT valuation reconstruction works, e.g. NVDA `trailingPE=32.48`). TSLA cut only by a harness time cap.

---

## 4. T2 — Walk-forward backtest ✅ (harness) / 🟡 (numbers)

**Design.** Expanding-window walk-forward: 8 tickers × 9 weekly `as_of` dates (2026-04-24 … 2026-06-19)
= **72 cohorts**, horizons 14/28/84d, benchmark SPY. `LT_LLM_CACHE=true`, `LT_MAX_REFLECTION=0`.
**OOS check passed** — single frozen provenance stamp
(`deepseek/deepseek-v4-pro`, temp 0, reflection 0, lookback 60, git bf68a0d).

| Metric | **2w (14d)** | **4w (28d)** | 12w (84d) |
|---|---|---|---|
| traded / graded | 19 / 18 | 19 / 15 | 19 / 0 (not matured) |
| win rate | 61%  CI [39%, 83%] | 47%  CI [20%, 73%] | — |
| mean excess vs SPY | **+3.50%**  CI **[+0.18%, +7.18%]** | +4.02%  CI **[−1.37%, +10.77%]** | — |
| median / IQR | +2.14%  [−1.51%, +6.19%] | **−1.07%**  [−4.38%, +7.95%] | — |
| Sharpe (per-cohort) / maxDD | 0.83 / −1.0% | 0.72 / −3.7% | — |
| conviction calibration | **non-monotonic** | **non-monotonic** | — |
| FDR survivors (BH 5%) | **2**: AMD, MSFT | **0** | — |

All CIs are seeded percentile bootstrap (n_boot=2000, 2.5/97.5 pct), deterministic. The min-n gate flags
**ESTIMATE (n<30)** on every horizon.

**Reading the numbers.**
- **2w** hints positive (mean +3.50%, CI *barely* excludes 0). **4w** the edge evaporates — CI includes 0,
  **median is negative**, mean driven by outliers (max +38%).
- **Concentration:** the +3.50% (2w) is carried by a handful of trades — three AMD LONGs (+19.4/+15.1/+15.0)
  and a META SHORT (+13.0). Remove AMD and it collapses; FDR keeps only AMD/MSFT at 2w, **none at 4w**.
- **Conviction is not predictive** — the top bucket (0.85+) *lost* at both horizons; the biggest winner
  (AMD +19%) had the *lowest* conviction (0.55). B3 gate: **do not size by conviction.**

**Return vs risk-free (RF≈4.5%/yr).** Raw directional return (LONG `+stk`, SHORT `−stk`):
- 2w: **+4.25%/trade** (median +2.10%, σ 7.83%); minus RF ≈ +4.08%; 11/18 beat RF.
- 4w: **+5.12%/trade** (median +1.42%, σ 13.17%); minus RF ≈ +4.77%; 9/15 beat RF.
- ⚠️ **Annualized figures (+111% / +197%) are a mirage** — they extrapolate 18 trades, one regime, two
  months, outlier-driven. And **most of the "premium over RF" is bull-market beta**: SPY itself returned
  +2.32%/2w vs RF +0.17%/2w, so the strategy's raw return is only ~+1.9% above SPY. Per-trade Sharpe over
  RF ≈ 0.52 (2w) / 0.36 (4w) — modest. The meaningful claim is alpha over SPY (the +3.50% excess), which
  is weak and not FDR-robust.

---

## 6. Data-quality findings (why the numbers are contaminated) ⚠️

Around **2026-06-15 the data pipeline stopped updating** — everything except the news parser:

| Feed | State | Impact |
|---|---|---|
| News + graph | fresh (5-min loop), container up 4d, 0 restarts | OK |
| **Daily prices** (`stock_prices_daily`) | **frozen at 2026-06-15** (SPY too) | **grading contamination** — trades whose horizon ends after 06-15 were graded over a *truncated* window → distorted excess |
| **Fundamentals — yfinance** | only 2 snapshots (06-15 full, 06-22 partial/21 tickers) | 8 of 9 cohort dates ran **EDGAR-only** → no forwardPE / analyst estimates / **`expectation_gap`** (the "alpha" signal) → **backtest ≠ live** |
| **Fundamentals — EDGAR** | latest filing 06-15 | event-driven; fine, but fetcher not checking for new |
| Embeddings | recurring `Batch embedding failed: No embedding data received` in log | fresh articles may lack vectors → weaker corpus/similarity |
| Long-term scheduler | last run 2026-06-23; container absent | forward accumulation stalled |

**Root cause:** the weekly job `scheduled_fundamentals_fetch` (daily-price top-up **+** EDGAR+yfinance) is
gated by `FETCH_FUNDAMENTALS`, which defaults **false** and is unset in `.env`; `SEC_EDGAR_USER_AGENT` is
also unset. So the job was never scheduled. **One flag fixes both** the stale prices and stale fundamentals.

---

## 7. Caveats on this report

1. Single regime (bull) — no bear/flat cohorts.
2. n<30 at every horizon — bootstrap CIs are wide; do not treat as a verdict.
3. Outcome prices stale at 06-15 → later cohorts graded over truncated horizons (§6).
4. Fundamentals asymmetry → backtest fundamentals poorer than live (no `expectation_gap` for 8/9 dates).
5. Neo4j 7688 external port dropped intermittently under load (driver retried; corpus mostly intact).
6. 12w horizon ungraded (not matured).

---

## 8. Verdict & next steps

**Machinery: validated.** The agent runs end-to-end, is reproducible, respects no-lookahead, and the whole
B0–B4 methodology stack executes and reports honestly. No bugs surfaced during the 72-cohort sweep.

**Strategy: unproven** (and not provable on this data). Weak, concentrated, single-regime, uncalibrated,
and contaminated by stale outcomes.

**Do next, in order:**
1. **Restore the data pipeline** (prereq for any trustworthy result): set `FETCH_FUNDAMENTALS=true` +
   `SEC_EDGAR_USER_AGENT`, recreate the news container, one-off catch-up
   (`daily_price_fetcher.py --period 1mo`, `fundamentals_fetcher.py`). Fix the embedding failures.
2. **Re-run the backtest** once prices reach today and fundamentals are current — removes the §6
   contamination.
3. **Deploy** the long-term scheduler so forward paper-testing (B4) accumulates across regimes — the only
   path to a real verdict (meaningful +4w stats ~1 month out; calibration ~a quarter).
4. Deferred: news-archive backfill (B2) for pre-2026-02 regimes; conviction re-calibration once n grows.

Until then, every number ships with its **n** and a **single-regime / smoke-test** caveat.

---

## Addendum 2026-07-10 — step 1 (data pipeline) done

- Fundamentals + daily-price weekly job confirmed **running on schedule** (Sat 03:00 ET;
  last run 2026-07-04). The "disabled" status above is outdated.
- Root cause of the frozen benchmark found & fixed: the weekly job excluded **SPY** (bare
  `TOP_50_TICKERS` arg). `stock_prices_daily` incl. SPY backfilled and fresh through
  2026-07-09; fix must be **redeployed** into the news-fetcher image to persist
  (see DEPLOY.md §8).
- Live grading tables audited: **0 rows** in `shift_library` / `longterm_agent_results`
  were graded against frozen prices — §6 contamination is confined to this backtest's
  numbers. Step 2 (re-run the backtest on healed prices) remains open.
