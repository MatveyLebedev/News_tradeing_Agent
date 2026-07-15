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
| 11 | 2026-07-15 | `wf_30x_f12_deepv2` | `9f8d601` | 30×9 | deep v2 (planner+parallel+citations) | *(running)* | decision rule: must beat #8 AND #9 → `LT_DEEP_V2=true` |

## Queued / future

- **Consensus-in-verdict A/B** — wire `get_street_consensus` data into the fundamentals
  prompt (prompt change → own id), after #11 settles.
- **`LT_FEWSHOT` A/B** — graded-analog few-shot in synthesis; wait for a thicker graded
  analog base.
- **v3 re-examination ~2026-08-06** — not a new sweep: `grade_shifts` + report once the
  July semis-wave cohorts mature (the window v3 exists for).
- **12w horizons** of ids #8-11 mature through ~2026-09-11 — re-run grade (idempotent).
