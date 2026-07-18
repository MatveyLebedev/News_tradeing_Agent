# Backtest ledger

One row per `backtest_id` in `backtest_runs` — the immutable index of every sweep ever
run. Detailed write-ups live in the linked docs; raw rows in the DB. Conventions:

- **id is append-only**: config changes mid-protocol → new id (OOS discipline: one
  provenance stamp per id; the report warns otherwise).
- 4w hold-book metrics quoted (win / mean excess vs SPY / per-cohort Sharpe) unless
  noted; stopped book = Phase 5.5 structural 10% stop on the same trades.
- Standard 30x grid: 30 tickers × 9 weekly cohorts 2026-04-24→06-19, horizons 2w/4w/12w,
  benchmark SPY, cache ON, reflection 0, asof-today 2026-07-10.
- All results to date are ONE-REGIME (bull; vix<20 / hy<p50 splits confirm) — deltas are
  directional evidence, CIs on means include zero everywhere so far.

| # | date | backtest_id | tree | grid | config delta | 4w result (win / mean / Sharpe) | verdict / where detailed |
|---|---|---|---|---|---|---|---|
| 1 | 2026-07-08 | `wf_2026q2_v0.1` | `bf68a0d` | 8×9 | detection v1 pilot | protocol pilot, superseded by 30x | harness bring-up (B1) |
| 2 | 2026-07-10 | `wf_2026q2_v0.2` | `2a69f07` | 8×9 | detection v2 gates | 2w +0.98% (gate run) | VALIDATION_REPORT_v0.2.md |
| 3 | 2026-07-11 | `wf_30x_v0.1` | `5c5fd88` | 30×9 | detection v1, wide | 49% win, Sharpe −0.29; "edge" was AMD concentration | VALIDATION_REPORT_v0.2.md (wide check) |
| 4 | 2026-07-11 | `wf_30x_v0.2` | `5c5fd88` | 30×9 | detection v2, wide | 55% / −0.54% / 0.57; worst −40.8% | baseline for everything below |
| 5 | 2026-07-11 | `wf_30x_v0.2_ct` | `fb7eda5` | 30×9 (replay) | + counter-trend guard | guard kept (removed 0 good trades) | docs/GATE_AB_2026Q2.md |
| 6 | 2026-07-11 | `wf_30x_v0.2_g2` | `fb7eda5` | 30×9 (replay) | table conviction gate | table gate REJECTED (kept −63.6% of SHORTs, dropped +39.7% of LONGs) | docs/GATE_AB_2026Q2.md |
| 7 | 2026-07-11 | `wf_30x_v0.2_g2ct` | `fb7eda5` | 30×9 (replay) | table gate + guard | best-behaved but 4w negative → not switched | docs/GATE_AB_2026Q2.md |
| 8 | 2026-07-12/15 | `wf_30x_f12` | `82f1ff8` | 30×9 | + Phase 12 fundamentals v2 | 57% / **+0.53%** / 0.91 | ✅ Phase 12 validated · BACKTESTS_2026Q2_PH6_12_13.md §1 |
| 9 | 2026-07-12/15 | `wf_30x_v0.2_deep` | `82f1ff8` | 30×9 | + deep analysis v1 ON | 62% / **+1.78%** / 1.30; calibration monotone | ✅ 6-pre verdict: nudge HELPS · §2 |
| 10 | 2026-07-15 | `wf_30x_f12_v3` | `82f1ff8` | 30×9 | detection v3 (sector-relative) | 59% / +0.55% / 0.95; 2w degrades | ⏸ no cutover; re-exam ~2026-08-06 · §3 |
| 11 | 2026-07-15 | `wf_30x_f12_deepv2` | `9f8d601` | 30×9 | deep v2 (planner+parallel+citations) | 58% / **−0.91%** / 0.39; 2w −0.96%/−0.04 | ❌ **REJECTED** — loses to BOTH #8 and #9; `LT_DEEP_V2` stays false, deep v1 remains live |
| 12 | 2026-07-17 | `wf_30x_f12_cons` | `59f100d` | 30×9 | consensus-in-verdict (retro = surprise-history-only block) | 59% / +0.11% / 0.45; 2w −0.19%/0.34; **4w calibration turns MONOTONIC** (top bucket 79% win, +4.33%) | ❌ flag stays false for now — decision delta −19.6pp, dominated by ONE re-introduced counter-trend INTC short (−40.8%); UBER flips were RIGHT (+36.6pp). See below. |

## #12 consensus-in-verdict — detail (2026-07-17)

Config delta vs #8: `LT_CONSENSUS_IN_VERDICT=true`, deep OFF, tree `59f100d` (stage B +
retro fallbacks — 119/119 retro coverage via FMP→yfinance surprise-history chain).
RETRO CAVEAT: pre-2026-07-15 as-ofs see only the degraded block (realized surprises; no
forward estimates / estimate trend) — the live feature is only PARTIALLY tested here.

Decision-level vs #8 (42 shared, 7 dropped, 6 added, 4 flips; 4w signed):
- **Flips were right: +36.6pp.** UBER LONG→SHORT twice (+8.2, +11.3 — chronic-miss
  history correctly overrode a bullish read), MSFT SHORT→LONG (+0.4).
- **Added trades: −55.2pp, of which ONE trade is −40.8pp**: INTC 2026-04-24 SHORT —
  the surprise-history ("chronically misses") re-created the counter-trend INTC short
  that f12 had eliminated, straight into the April rebound. Without it added = −14.4pp
  (AVGO/GOOGL/PFE longs mildly negative).
- Dropped: ≈wash (+1.0pp forgone; lost AMD +20.3 but shed BAC/TSLA losers).
- **Structural finding: 4w conviction calibration becomes MONOTONIC** (0.55-0.65:
  −2.68% → 0.75-0.85: +4.33%, 79% win) — first run where LLM conviction is
  sizing-trustworthy; baseline's top bucket was its worst. 2w stays non-monotonic.

Verdict: net evidence negative → flag stays `false` live. But the failure mode is
specific and guardable: surprise-history should not justify a NEW counter-trend short
on its own. Candidate stage B.2 (own id): consensus block + an explicit instruction
that chronic-miss history NEVER initiates counter-trend direction, only adjusts
conviction — re-run when forward snapshots cover live cohorts (~2026-08+), where the
un-degraded block (forward estimates + trend) is what actually ships.

## Anti-overfit protocol (adopted 2026-07-18)

The research loop "see a failure → design a fix → test on the same data" is data
snooping by construction. These rules bound it:

1. **Burned-grid rule.** A feature designed by looking at the results of window X can
   only be REJECTED by a re-run on X (do-no-harm filter) — never ACCEPTED. Acceptance
   requires cohorts that did not exist when the feature was formulated (the grid grows
   one cohort per week, so the OOS window builds itself).
2. **Attempt counter.** Every sweep on a window burns a comparison. The standard
   2026-04-24→06-19 grid has now hosted **13 ids** (#3–#13) — any "best of 13" on it
   must be read with that denominator in mind. New windows reset the counter.
3. **Hindsight guard.** A fix must be formulated as a general mechanism (structural
   prior), not as the observed instance; it gets ONE formulation (n=1 failure → one
   broad rule, no iterating variants against the same window), and it is written into
   this ledger BEFORE its backtest runs (pre-registration).
4. **Standing rules** (already in force, restated): flags default OFF and prompts stay
   byte-identical when off; acceptance needs positive decision-level evidence, "not
   worse" keeps the incumbent; binary architecture decisions over numeric knob fits;
   final arbiter is the forward book, retro is only a filter.

## Queued / future

- **Consensus stage B.2 — PRE-REGISTERED 2026-07-18, retro run = do-no-harm only.**
  Formulation (frozen before the run): the #12 block + one guard sentence — surprise
  history modulates conviction only, never initiates direction (especially counter-trend
  after a large recent move). Hypothesis: keeps the UBER-class flips (verdict-grounded),
  drops the INTC-class initiations (history-only). Retro id `wf_30x_f12_cons2` on the
  burned grid can only REJECT (rule 1). ACCEPTANCE criteria, fixed now: on cohorts
  dated after 2026-07-17 (August re-run with forward-covered snapshots), decision-level
  delta vs f12 ≥ 0 AND no new counter-trend initiation losses > stop distance AND 2w
  book not degraded. One formulation only (rule 3) — if it fails, the feature is
  parked, not iterated.
- **`LT_FEWSHOT` A/B** — graded-analog few-shot in synthesis; wait for a thicker graded
  analog base.
- **v3 re-examination ~2026-08-06** — not a new sweep: `grade_shifts` + report once the
  July semis-wave cohorts mature (the window v3 exists for).
- **12w horizons** of ids #8-11 mature through ~2026-09-11 — re-run grade (idempotent).
