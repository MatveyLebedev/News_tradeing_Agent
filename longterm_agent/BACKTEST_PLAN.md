# Long-Term Agent — Backtesting: Capability Analysis & Plan

**Question:** can this agent be backtested, and how good would the backtest be?

**Verdict (TL;DR).** The *hard, correct foundation is already built* — the agent is architected
for backtesting: every data read is gated by `as_of`, so the **same code runs live and in the
past with no separate "backtest mode"** (the usual #1 source of leakage is designed out). The
replay/grade/report scripts exist and run. But three things block a **trustworthy** backtest
today, in priority order:

1. **Reproducibility is broken** — the LLM synthesis conviction varies run-to-run (observed:
   NVDA `NEUTRAL 0.2 → LONG 0.75` on an identical re-run), so the *trade set* isn't
   reproducible and config A/Bs can't be compared. *Cheap to fix, highest leverage.*
2. **News history is shallow** — dense news only since **2026-02-23**, giving ~8–9 valid 60d
   cohorts. *The real unlock; expensive (needs a news archive).*
3. **Fundamentals PIT is asymmetric** — EDGAR is true point-in-time back to 2019, but yfinance
   is a **single forward snapshot (2026-06-15)**, so historical backtests run with *poorer*
   fundamentals than live (no valuation ratios / analyst estimates before the snapshot date).

So: backtesting is **possible now as a harness/smoke test**, but **not yet statistically
meaningful**. Fix order below: reproducibility → harness+metrics → data depth → formal
walk-forward, with **forward paper-testing as the primary validation path** meanwhile.

---

## 1. What a real backtest needs — current scorecard

| Capability | Needed for | Status today |
|---|---|---|
| **No-lookahead engine** | trustworthy any backtest | ✅ **Strong** — `as_of` gates every reader; one code path live+past |
| **Reproducibility** | comparing configs, trusting a number | ❌ **Broken** — LLM conviction variance → non-deterministic trades |
| **News depth & density** | enough cohorts, multiple regimes | ⚠️ **Weak** — dense only since 2026-02-23 (~8–9 cohorts, one regime) |
| **Fundamentals PIT** | the financials half of the thesis | 🟡 **Mixed** — EDGAR true-PIT to 2019; yfinance forward-only (1 snapshot) |
| **Outcome/price data** | grading returns vs benchmark | ✅ **OK** — 2y daily bars, 120 tickers + SPY |
| **Statistical power** | a verdict, not noise | ❌ **Weak** — n≈17 shifts / 3 trades; need hundreds |
| **Metrics & report** | calibration, risk-adjusted, per-regime | 🟡 **Partial** — win-rate/excess/hit-rate exist; missing the rest |
| **Cost/throughput** | running the universe × many cohorts | 🟡 **OK-ish** — ~3 min/ticker; bucket cache helps; LLM cache would help re-runs |

---

## 2. What already exists (build on these, don't duplicate)

- **`as_of` discipline** — news `published`, EDGAR `filed_date`, yfinance `fetch_date`, prices
  `date` all filtered `≤ as_of`. This is *the* enabler and it's done.
- **`backfill_shift_library.py`** — detection-only replay (corpus→profiling→shift) walking
  `as_of` weekly; fast path (skip reduce, `characterize=False`) + `bucket_summary_cache`.
- **`full_signal_backtest.py`** — whole pipeline → signal → graded vs SPY; two-phase (run, then
  grade with a fresh cursor to dodge connection staleness).
- **`grade_shifts.py` / `grade_results.py`** — fill realized +2/4/12w returns + SPY benchmark.
- **`backtest_report.py`** — detection hit-rate (sign of excess vs shift direction).
- **`shift_library`** — forward-accumulating case base, leakage- and version-filtered.

The **machinery is real**. The gap is reproducibility, data, metrics, and statistical power.

---

## 3. The three blockers, concretely

### 3.1 Reproducibility (the LLM determinism gap)
The decision *spine* is deterministic (shift `z`-score/`shift_score` are rule-based and were
identical across re-runs), but the **synthesis conviction** is free LLM judgment, and at
`temperature=0` reasoning models still vary run-to-run — most in the **no-shift** cell where the
matrix gives weak guidance. Because `traded` depends on `conviction ≥ 0.55`, the *actionable set*
flips between runs. **Consequence:** a backtest number isn't reproducible, so you can't tell
whether a config change helped or you just re-rolled the dice.

### 3.2 News depth (real DB state)
```
dense news begins 2026-02-23  (~4/week before → ~1,800/week after)
full 60d-dense window ⇒ as_of ≥ ~2026-04-24  ⇒ ~8–9 weekly cohorts to today
full 90d-dense window ⇒ as_of ≥ ~2026-05-24  ⇒ ~4 weekly cohorts
```
Everything available is **one market regime** (a rally). No amount of cleverness makes 8 cohorts
of one regime generalize. Importing a **news archive** is the only way to backtest deeper history.

### 3.3 Fundamentals PIT asymmetry
```
EDGAR  filed_date: 2019-07-18 → 2026-06-15   (true PIT — fine for any as_of)
yfinance fetch_date: 2026-06-15 only          (forward snapshot just started)
```
For a backtest at `as_of = 2026-05-01`, the yfinance snapshot (`fetch_date 2026-06-15`) is
correctly filtered out — so the backtest sees **EDGAR statements only**: no PE/PS, no analyst
revisions, no expectation gap. The **live path is richer than the backtest path**, which biases
any historical comparison. (Partial fix in §4 B2: reconstruct trailing PE/PB from EDGAR equity ×
daily price — computable from data we already have.)

---

## 4. The plan

Phased by leverage. Each phase has a deliverable and an acceptance bar. B0–B1 are cheap and make
*every* future number trustworthy; B2 is the expensive real unlock; B4 runs in parallel from now.

### B0 — Make backtests reproducible & auditable *(small, do first)* — ✅ **DONE**
- ✅ **LLM response cache** (`llm_cache.py`, `create_llm_cache_table.sql`). A Postgres-backed
  `BaseCache` installed as LangChain's *global* cache, keyed by `sha256(model+params+prompt+schema)`.
  Because every node calls the model through `with_structured_output(...).invoke(...)` and the
  global cache intercepts below it, **all** LLM calls (map/reduce/shift/fundamentals/synthesis/
  reflection) are cached with **zero node changes**. Record-then-replay: the first run of a cohort
  records each unique call, later runs replay → deterministic + ~zero token cost. A hit requires a
  byte-identical prompt (which embeds the news/financial text), so it never returns a stale answer
  for changed inputs — safe to enable anywhere. Toggle `LT_LLM_CACHE=true`; wired into
  `run_longterm_agent`, `batch_run_longterm`, `full_signal_backtest`, `backfill_shift_library`.
  - ⚠ **Structured-output caching fix** (`config.make_llm` → `_StructuredFnChatOpenAI`): the
    native `json_schema` structured-output path stashes a non-serializable parsed Pydantic object
    in `additional_kwargs['parsed']`, which makes the cached payload un-revivable (`loads()` raises)
    → permanent cache miss → non-reproducible. We force `method="function_calling"` for every
    structured call, so the response carries serializable `tool_calls`, the cache round-trips, and
    cached replays match live decisions. Live and backtest use the same method.
  - Determinism also requires **byte-stable prompts**: the corpus is sorted by `(published, id)`
    and graph-sentiment ties break via `sorted(set(...))` (`news_corpus_tool.py`), so map/reduce
    prompts are identical run-to-run and hit the cache.
- ✅ **Leakage canary** (`tests/test_leakage_canary.py`). For a fixed `as_of`, asserts no reader
  returns data dated `> as_of`: news corpus (`published`), EDGAR (`filed_date`), the yfinance PIT
  asymmetry (valuation empty before the first snapshot — §3.3), and technicals (last bar). The
  shift_library analog window/version filter stays covered by `test_shift_library.py`.
- ✅ **Backtest profile / provenance** (`config.provenance()`): stamps `git_revision`, model,
  temperature, reflection iters, `llm_cache`, lookback — printed by the backtest entrypoints so
  runs are comparable. Run backtests with `LT_MAX_REFLECTION=0`.
- ✅ **Acceptance verified:** NVDA `as_of=2026-06-21`, `LT_MAX_REFLECTION=0`, run twice in one
  process — run1 (cold, records) 257.9s → run2 (warm, replays) 29.1s = **8.9× faster**, and the
  signal fingerprint is **byte-identical** (`b272cf658daa5a1c`). An instrumented-cache harness
  confirmed the per-bucket map/reduce calls go `STORE → HIT` with identical prompt hashes. 46 unit
  tests pass incl. the canary.

### B1 — Consolidated harness + real metrics *(small/medium)* — ✅ **DONE**
- ✅ **One walk-forward driver** (`backtest.py`): expanding-window, configurable universe /
  cohort step / horizons, **writing to the separate `backtest_runs` table**
  (`create_backtest_runs_table.sql`) namespaced by a `backtest_id` — so backtests never pollute
  the live `longterm_agent_results` / forward paper-trade stats. Stamps `provenance` (B0) per row;
  the pipeline runs once per `(ticker, as_of)` and writes a row per horizon (+2/+4/+12w graded
  independently). Resumable (skips recorded cohorts) and reproducible via the B0 LLM cache. A
  separate grading pass fills realized outcome + regime (`_ret` vs SPY).
- ✅ **Report** + a pure, unit-tested metrics module (`backtest_metrics.py`):
  - hit-rate **by conviction bucket** → a **calibration/reliability curve** (is conviction
    predictive? — directly tests the §3.1 concern);
  - excess-return **distribution** (mean/median/IQR/min/max), not just the mean;
  - **Sharpe / information ratio** of the per-cohort equity curve + **max drawdown**;
  - splits by **direction**, **shift vs no-shift**, and **regime** (SPY trailing return);
  - **percentile bootstrap CIs** (deterministic, seeded) for win-rate and mean excess, with a
    prominent **n** and a **min-n gate** (n<30 ⇒ "ESTIMATE — harness check, not a verdict").
- ✅ **Acceptance verified:** a single command
  (`python -m longterm_agent.backtest --backtest-id <id> --tickers … --start … --end … --horizons …`)
  runs → grades → prints a versioned report with CIs to `backtest_runs` (isolated from live).
  End-to-end exercised against real prices (grading + regime + all metrics render, min-n gate
  fires); 8 metrics unit tests; **64 tests pass**.

### B2 — Data depth *(the real unlock; larger)*
- **News archive import** — the gating dependency for pre-2026-02 backtesting. Until this lands,
  **historical backtest depth is capped at ~8 cohorts**.
  - ✅ *Source-evaluation spike done* (`news_archive_spike.py`): GDELT DOC 2.0 (free, no key,
    browser-UA + ≥7s pacing required) vs the live `news` feed. **Verdict: depth-yes, parity-no.**
    - *Depth:* GDELT supplies ~140–250 on-topic articles/quarter/ticker for Sep–Dec 2025 where our
      table has **0** — the pre-feed gap is fillable.
    - *Parity (the catch):* GDELT's mix is **CNBC-heavy** (e.g. NVDA cnbc 234 : bloomberg 16) while
      the live feed is **Bloomberg-dominant** (~23k bloomberg : ~3k cnbc) — the source
      distributions are essentially **inverted**, creating a seam at the 2026-02 boundary. URL
      overlap with our RSS in a shared window is low (AAPL 23%, JPM/TSLA 6–7%): GDELT samples a
      *different* stream. And GDELT returns url+title+date only (no summary/full_text); Bloomberg
      is paywalled, so full-text backfill mostly fails → title-only embeddings.
  - *Implication:* a naive GDELT backfill unlocks depth but biases backtest-vs-live comparisons
    (source-mix shift). Sound options: (a) backfill but **tag `source='gdelt'`** + flag a
    source-era caveat in the B1 report (and prefer fetchable CNBC/MarketWatch full text); or
    (b) a **paid archive** (Event Registry / NewsAPI.ai — full text + better Bloomberg coverage),
    which is the news analog of the deferred FMP/Sharadar upgrade. Decision pending.
- **Fundamentals**:
  - ✅ *Cheap partial fix — DONE* (`fundamentals_tool.valuation_pit`): reconstruct **trailing
    PE / P/B / P/S** at any `as_of` from EDGAR (as-filed TTM) × the daily close. TTM dollar
    figures are rebuilt from the latest annual + stub quarters − matching prior quarters (the
    standard FY-anchored roll, so no fake partial year), and diluted shares are implied from one
    period's `net_income / eps_diluted` (split-immune; ratios go through market cap). Both inputs
    are point-in-time, so valuation is available at **any** `as_of` — including *before* the first
    yfinance snapshot — and is recomputed at the analysis date rather than frozen at a fetch date.
    `read_fundamentals` overlays the PIT keys on the (forward-only) yfinance snapshot; the rest of
    the snapshot (forwardPE, margins, growth, ROE) stays as supplemental context. **No fetcher
    change / re-fetch.** Verified: NVDA `as_of=2026-06-21` reconstructed `trailingPE=32.48` vs the
    live yfinance `32.53` (~0.15%); 5 new tests in `test_fundamentals_pit.py` (sanity, PIT-price,
    determinism, TTM-vs-manual, asymmetry-fixed) + updated leakage canary; 55 tests pass.
  - *Optional upgrade (deferred — not now):* FMP Premium (~$59) / Sharadar SF1 for historical
    ratios **and** PIT consensus estimates; because the fetcher writes normalized JSONB read by
    `fundamentals_tool`, swapping the source is a **fetcher-only** change.
- **Acceptance:** valid-cohort count grows beyond one regime *(needs the news archive)*; backtest
  fundamentals ≈ live fundamentals (no path asymmetry) — **met for valuation** via `valuation_pit`.

### B3 — Formal walk-forward validation protocol *(medium)* — ✅ **DONE**
- ✅ **Protocol doc** (`WALK_FORWARD_PROTOCOL.md`): expanding-window walk-forward, weekly cohorts,
  +2/+4/+12w horizons, SPY **and** sector-ETF benchmarks, out-of-sample discipline, multiple-testing,
  calibration gate, per-regime reporting, the repeatable command, and a 6-point verdict bar.
- ✅ **Enforced in code** (`backtest.py` report + `backtest_metrics`):
  - **OOS discipline** — the report fails if a `backtest_id` carries >1 `provenance` stamp (config
    changed mid-sweep ⇒ not out-of-sample); one frozen config per id.
  - **Multiple-testing** — per-ticker one-sided bootstrap p-value (H0: mean excess ≤ 0) +
    **Benjamini–Hochberg FDR at 5%**; reports survivors, not raw positives.
  - **Calibration gate** — `is_monotonic` on the conviction reliability curve →
    "sizing-trustworthy: YES/NO" before conviction is used to size.
  - **Benchmark configurable** (`--benchmark`, default SPY) — sector-ETF leg ready; *data-gated*
    (only SPY is in `stock_prices_daily` today).
  - New pure helpers (`is_monotonic`, `bootstrap_pvalue`, `benjamini_hochberg`) are unit-tested.
- ✅ **Acceptance:** documented, repeatable protocol; results reported OOS with CIs and the three
  gates (OOS / FDR / calibration). Verified end-to-end on seeded data; 74 tests pass.

### B4 — Forward paper-testing *(primary path — runs from today)* — ✅ **DONE (formalized)**
Given thin history, the **statistically honest** validation is forward accumulation, which Phase 6
already does: the weekly batch writes every signal to `longterm_agent_results`, `grade_results`
fills outcomes as horizons mature. Formalized in `forward_report.py`:
- ✅ **Equity curve / cumulative excess** over the live graded trades, **reusing the B1 metrics**
  (`backtest_metrics`) so forward and backtest speak the same language: per-cohort mean excess →
  cumulative curve, Sharpe/IR, max drawdown, win-rate + mean with **bootstrap CIs**, distribution,
  conviction calibration, direction/shift splits, and the min-n gate.
- ✅ **Cohort-maturity tracker** (`cohort_maturity`): matured vs pending counts, next-maturity date,
  and an "matured-but-ungraded" nudge — so "we don't have the numbers yet" is explicit, not hidden.
- ✅ **`/trades` bot view** shows a compact forward block (`forward_report.bot_summary`): cumulative
  excess · Sharpe · max DD · pending + next maturity. Verified against live (1 pending, next
  ~2026-07-20) and a populated synthetic path (equity curve, Sharpe, CIs render); 65 tests pass.
- **Timeline:** first +4w numbers ~1 month out (~4 fresh cohorts); meaningful +4w stats + first
  +12w reads ~1 quarter out; conviction **calibration** realistically needs a full quarter.

---

## 5. No-lookahead audit checklist (the leakage canary, B0)

For a frozen `as_of`, assert each reader returns only `≤ as_of`:
- [ ] `news_corpus_tool` — `published ≤ as_of` (graph + regex paths)
- [ ] `fundamentals_tool` — EDGAR `filed_date ≤ as_of`, yfinance `fetch_date ≤ as_of`
- [ ] `technical_tool` / prices — `date ≤ as_of`
- [ ] `shift_library_tool.find_similar_shifts` — analog `outcome_filled_at`/window completed `< as_of` **and** version-scoped
- [ ] `bucket_summary_cache` — week-*W* summary uses only articles published within *W*
- [ ] grading (`grade_*`) is the **only** place that reads `> as_of` (intentional: outcome, not decision)

---

## 6. What "trustworthy" looks like (exit criteria)

A backtest result is reportable as a *verdict* (not a smoke test) only when **all** hold:
1. Re-running the cohort reproduces the trade set (B0). 
2. ≥ ~150–300 graded trades spanning **≥ 2 regimes** (B2 + B4).
3. Conviction calibration curve is monotonic (B3).
4. Excess return is positive **net of turnover** with a bootstrap CI excluding 0 (B1).
5. Backtest and live fundamentals paths are symmetric (B2).

Until then, every number ships with its **n** and a "single-regime / smoke-test" caveat, exactly
as the current scripts already print.

---

## 7. Effort & sequencing

```
B0 reproducibility + canary   ▓▓        small   ✅ DONE
B1 harness + metrics          ▓▓▓       small/med ✅ DONE
B4 forward paper-testing      ▓         ✅ DONE (formalized; now accumulating from today)
B2 news archive + fund. PIT   ▓▓▓▓▓▓    large   ← fundamentals PIT ✅; news archive spike done (depth-yes/parity-no)
B3 walk-forward protocol      ▓▓▓       ✅ DONE (doc + OOS/FDR/calibration gates enforced in code)
```

**Bottom line:** the whole methodology stack is now built — **B0** (reproducibility), **B1**
(harness + metrics), **B2-valuation** (PIT fundamentals), **B3** (walk-forward protocol with
OOS/FDR/calibration gates), and **B4** (forward paper-testing). Every number is reproducible,
isolated from live, ships with CIs + a min-n gate, and is checked for out-of-sample discipline,
multiple-testing, and calibration. The **one remaining unlock is data depth** — a news-archive
backfill (the spike found GDELT gives depth but not source parity, so it needs a paid archive for
a clean historical sample). Until that lands, **forward paper-testing (B4) is the honest
validation path**, and it's already accumulating from today.

---

## 8. First execution (2026-07-01) — what running it revealed

The harness was **run end-to-end for the first time**: `backtest_id=wf_2026q2_v0.1`, 72 cohorts
(8 tickers × 9 weekly dates 2026-04-24…06-19), horizons 14/28/84d. Full write-up in
**[VALIDATION_REPORT.md](VALIDATION_REPORT.md)**. Confirmed: the machinery works (OOS check passes,
FDR/calibration gates fire, min-n gate flags ESTIMATE, reproducible). The result is **not a verdict**
(single bull regime, n<30, edge concentrated in AMD/MSFT, conviction non-monotonic).

Running it also surfaced a **new blocker not in the plan above — a stalled data pipeline** (a distinct
issue from B2 data *depth*):

- **Stale outcome prices.** `stock_prices_daily` froze at 2026-06-15, so any trade whose horizon ends
  after 06-15 was graded over a *truncated* window → **contaminated excess** for later cohorts. Grading
  cannot advance past the latest price date.
- **Stale fundamentals.** The yfinance forward snapshot exists only for 06-15/06-22, so 8 of 9 cohort
  dates ran **EDGAR-only** (no `expectation_gap`/forwardPE) — backtest fundamentals ≠ live.
- **Root cause:** the weekly `scheduled_fundamentals_fetch` (daily-price top-up + EDGAR + yfinance) is
  gated by `FETCH_FUNDAMENTALS`, which defaults **false** and is unset; `SEC_EDGAR_USER_AGENT` is also
  unset. So neither daily prices nor fundamentals refresh.

**Prerequisite for any trustworthy re-run:** restore the pipeline (enable `FETCH_FUNDAMENTALS`, set
`SEC_EDGAR_USER_AGENT`, one-off catch-up of prices + fundamentals to today), then re-run the sweep.
