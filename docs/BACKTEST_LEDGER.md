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
| 13 | 2026-07-18 | `wf_30x_f12_cons2` | `8b58451` | 30×9 (do-no-harm) | #12 + B.2 guard sentence | 51% / **−0.99%** / 0.30; 2w −1.04%/0.11; decision delta vs f12 **−68.2pp** | ❌ **REJECTED by its own filter — B.2 PARKED** (rule 3: one formulation). Guard did NOT remove the INTC short it targeted, and reshuffled 9 unrelated decisions as noise. See below. |
| 14 | 2026-07-19 | `monday_t1` | `7dde443` | 30×12 Mon 06:30 | deploy regime, `open_d0`, deep ON | 52% / +0.89% (n=31) | ⛔ **VOID** — launched pre-PIT-audit with F1–F5 open; harness check only. See below. |
| 15 | 2026-07-20 | `monday_t1_pit` | `b379757` | 30×12 Mon 06:30 | #14 re-run on leak-free code (pipeline 0.9.0) | 47% / **+0.18%** (n=30, **3 cohorts**); block CI [−9.61%, +1.64%]; 2w −0.82% (n=40) | ⚖️ **INCONCLUSIVE** — regime not validated, not rejected. The leak was worth ~+0.7pp (4w) / ~+1.4pp (2w). See below. |

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

## #13 stage B.2 — post-mortem (2026-07-18): REJECTED, feature parked

Pre-registered above; the retro do-no-harm filter rejected it same day. Two findings
worth more than the feature:

1. **The guard could not work: it references information the model does not have.**
   "Never initiate against a large recent price move" — but the fundamentals prompt
   carries 180d trend technicals only; the +24% catalyst day is invisible in that
   context. INTC 04-24 SHORT (−40.8%) traded ANYWAY — the targeted failure survived
   verbatim. A real fix for that class is a DETERMINISTIC risk-gate check on realized
   short-window price action (code, not prompt) — if ever pursued, that is a NEW
   feature with its own pre-registration, not a B.2 iteration.
2. **Prompt-noise floor measured.** One added sentence, everything else identical:
   9 of ~48 decisions reshuffled (dropped LLY +19.3 / AAPL +6.4 / MSFT +5.9, added
   mostly losers, flipped JPM) — net −48.6pp swing vs #12 from pure decision
   instability. Consequence for ALL future prompt-level A/Bs on this grid: deltas
   smaller than roughly ±1pp mean excess are UNRESOLVABLE against LLM decision
   variance; only mechanism-level evidence (does the targeted trade class change as
   predicted?) is admissible, headline deltas alone are not.

Consensus-in-verdict status after #12+#13: `LT_CONSENSUS_IN_VERDICT` stays false;
stage B (unguarded block) may still be re-examined on post-07-17 forward-covered
cohorts per #12's criteria; stage B.2 (guard sentence) is dead.

## Queued / future
- **`wf_30x_strong` — strong-model split A/B (pre-registered 2026-07-18, NOT yet run).**
  Mechanism: upgrade the model on exactly the three judgment-heavy advisory calls —
  synthesis (`LongTermSignal`), fundamentals verdict (`FundamentalsTrend`), deep-analysis
  reduce (`DeepAnalysisOutput`) — via the new `STRONG_LLM_MODEL` role (empty = off, wired
  in `agent/graph.py`). Candidate: `anthropic/claude-sonnet-5` (pinned, non-preview).
  Prompts stay byte-identical; detection labels untouched (map model unchanged); the three
  upgraded calls get NEW LLM-cache keys, everything else replays. Motivation is structural,
  not window-fitted: #4's anti-correlated conviction tails and the #8/#12 verdict error
  class both live on these calls. Cost guard (2026-07-18 analysis, measured token volumes):
  +106% per run, cap +200% — breached only if reasoning output ≥2× current volumes; cap
  the thinking budget and record actual usage with the run.
  Protocol: the standard grid hosts attempt #14 and can only REJECT (burned grid — this
  window's failures informed the stage choice); acceptance needs post-2026-07-18 forward
  cohorts. Per #13's noise floor, headline deltas within ±1pp are inadmissible — the
  pre-registered mechanism-level predictions are: (a) 4w conviction calibration stays/
  becomes monotone with the top bucket not the worst, (b) fewer high-conviction
  counter-trend calls in the decision delta, (c) no degradation of the #9 deep-nudge
  effect. ONE formulation: model choice and stage set are fixed here; no iterating
  variants (a different model or stage set = a new pre-registration).
- **`monday_t1` — deploy-regime grid (pre-registered 2026-07-19, launched same day).**
  Question: does the agent work in the regime it will actually trade — decide Monday
  pre-open on fully digested weekend news, fill at Monday's open? ONE formulation:
  cohorts Mondays 06:30 ET 2026-04-27..2026-07-13 (12; 4w fully matured through the
  06-15 cohort at launch, the rest mature forward), the standard 30-ticker wf_30x set,
  `LT_METRICS_VERSION=2 LT_DEEP_ANALYSIS=true LT_LLM_CACHE=true LT_MAX_REFLECTION=0`,
  PIT feature cutoff active (tools/pit.py — Monday's session is NOT an input), fill
  `open_d0` (Monday's own open; entry after every decision input), dual book:
  T+2 diagnostic (Tuesday open, hold-only, `*_t2` columns) grades the SAME decisions.
  Frozen criteria (before any results seen):
  (a) primary — traded 4w mean excess vs SPY > +1pp (the #13 noise floor) ⇒ regime
      validated; within ±1pp ⇒ inconclusive, no cutover claims; < −1pp ⇒ not validated;
  (b) day-1 value — paired per-trade Δ = excess − excess_t2: mean Δ ≥ +0.5pp with
      bootstrap 90% CI > 0 ⇒ keep morning-decide→Monday-open; otherwise switch the live
      plan to Monday-post-close→Tuesday-open (information dominates);
  (c) report-only mechanisms — share of traded signals whose driving news landed
      Fri 16:00–Mon 06:30; conviction calibration no worse than wf_30x_f12; per-cohort
      runtime (operational feasibility of an 06:30 live batch).
  No variant iteration on this window; a different day/time/config = new registration.
  Deploy note: adopting the regime live also means switching the live graders to
  `open_d0` and moving the scheduler — a separate ops change after the verdict.
  **Run 1 (`monday_t1`, 2026-07-19, `7dde443`/pipeline 0.8.0) — VOID as a verdict:**
  it launched hours before the PIT audit, so F1–F4 (deep tools) and F5 (post-entry
  document leak, sharpest exactly under `open_d0`) were open. Harness verdict only:
  360/360 runs, 0 errors, 43 traded; `open_d0` fills verified landing on the as_of
  Monday itself with the Memorial-Day cohort correctly falling through to Tuesday.
  Contaminated numbers, recorded so they are not silently re-derived later: 4w n=31
  win 52% mean excess +0.89% (per-trade CI [−2.84,+4.55]); 2w n=38 win 58% +0.61%;
  calibration non-monotonic at both horizons; 0 tickers survive BH FDR. Paired day-1
  value (criterion b): 4w mean Δ **+0.30pp** (90% CI [−0.84,+1.48], p(>0)=0.36, day-1
  helped 17/31), 2w mean Δ **−0.69pp** — i.e. the pre-registered ≥+0.5pp bar was NOT
  met, which under the frozen rule points to Monday-post-close→Tuesday-open. Directional
  only: the trade SET is contaminated, so the paired estimate is computed on trades a
  leak-free agent might not have taken. **Run 2 (`monday_t1_pit`, launched 2026-07-20,
  `b379757`/pipeline 0.9.0)** re-runs the identical grid and the identical frozen
  criteria on fixed code; it, not run 1, decides the regime and the day.
  **Run 2 verdict (2026-07-20): INCONCLUSIVE — no cutover, no rejection.**
  360/360 runs, 0 errors, 41 traded (11% of signals), no STALE-UNGRADED rows.
  - *(a) primary — NOT MET, inconclusive.* 4w traded mean excess **+0.18%** (n=30),
    inside the ±1pp band; the honest cohort-block CI is **[−9.61%, +1.64%] over just
    3 cohorts**. 2w: **−0.82%** (n=40, 5 cohorts, block CI [−3.60%, +0.32%]).
    0 tickers survive BH FDR at either horizon; calibration non-monotonic at both,
    with the TOP conviction bucket again the worst at 4w (0.75–0.85 → −3.48%),
    reproducing the #4 anti-correlation. 12w shows +6.19% but n=15 in ONE cohort —
    an ESTIMATE, not evidence.
  - *Leak size, the most useful number here:* same grid, same criteria, only the PIT
    fixes differ → 4w +0.89% → +0.18%, 2w +0.61% → −0.82%. **Roughly +0.7pp (4w) and
    +1.4pp (2w) of the apparent edge was lookahead**, and what remains is indistinguishable
    from zero. Every pre-audit deep-ON number in this ledger should be read with that
    scale in mind (#9 especially).
  - *(b) day-1 value — formally MET at 4w, but not trustworthy.* Paired Δ = +1.61pp
    (per-trade 90% CI [+0.48,+2.79], p=0.009; day-1 helped 21/30). BUT the 30 trades sit
    in 3 cohorts and 29 of them in TWO (04-27 n=15, 05-04 n=14) — a 3-block bootstrap
    cannot produce a meaningful interval, and 2w (5 cohorts, n=40) points the OTHER way:
    Δ = −0.71pp. Horizon-contradictory and cohort-starved ⇒ treat as no answer yet.
  - *(c) mechanisms.* **67% of traded 4w signals had news in the Fri 16:00 → Mon 06:30
    window** — the premise of the Monday-morning regime holds (the information is really
    there); it is the edge that is unproven, not the freshness. Runtime is operationally
    fine for an 06:30 batch.
  **Action: none.** Live stays on the current schedule under `open_t1`; the scheduler
  move and the live `open_d0` switch are NOT justified by this evidence. Revisit ~2026-08-10
  at zero LLM cost: 9 of the 12 cohorts have not matured at 4w, and the daily grader fills
  them automatically — the same frozen criteria then run on ~3× the cohorts. Note the grid
  is now burned for variant iteration (rule 1); only maturation, not new formulations.

### Pre-registered, awaiting their window (formulations frozen — see linked sections)

- **`sel_null` — does the selection stage add value at all? (pre-registered 2026-07-20,
  NOT yet run; runs BEFORE `adv_mom`).** Full plan: **docs/AB_PLAN_selection_stage.md**.
  Motivation, measured free from stored rows (all directional signals are graded, not only
  traded ones), 4w shifted directional: gated vs ungated books are `monday_t1_pit` +0.18%
  (n=30) vs **+1.36%** (n=42); `wf_30x_f12` +0.85% (n=46) vs **+1.20%** (n=59);
  `wf_30x_v0.2_deep` +2.34% (n=39) vs **+2.35%** (n=55). **The gate adds value in none of
  the three books and subtracts in two.** Splitting rejections by cause via stored
  `conviction_raw`: "LLM itself unsure" is noise (n=1–4), but "machinery cut it"
  (raw ≥ 0.55) returned +1.48% / +3.62% / +3.27% — i.e. the deterministic caps and penalties
  removed trades that beat the ones kept. Diagnosis: `conviction` is one scalar doing three
  jobs (eligibility caps, risk penalties, model belief) before a single 0.55 comparison, so
  no component is separately measurable.
  Frozen challenger, ONE formulation: `LT_SELECTION=vetoes_only` — trade every shifted
  directional signal passing the eligibility vetoes, with **no conviction threshold and no
  risk penalties**; incumbent = today's `llm` gate at 0.55; flag defaults to incumbent.
  Deliberately the NULL, not a better gate — testing `table` first would tune a mechanism
  whose usefulness is unestablished. Prerequisite **P2a**: stamp a `gate_trace` JSONB
  (raw/final, vetoes, per-penalty amounts, table inputs) — no behaviour change, and it makes
  every future gate hypothesis replayable offline for free.
  Stage 1 ($0 replay, burned books, reject-only): rejected if the challenger loses >1pp on
  any book, more than doubles trade count, or worsens the book's worst trade by >5pp (the
  penalties may be buying tail protection the mean hides). Stage 2 (~$4, unburned Mondays
  2026-01-05…04-20): accept only on >+1pp surviving the **cohort-block** CI **and net of
  transaction costs** — which makes plan item D2 a blocking dependency, since the challenger
  raises turnover ~40%.
  *Disclosure (rule 1):* this slice was chosen after seeing the outcomes ⇒ mined windows
  cannot accept it. Live gate stays ON regardless of stage 1.
- **`adv_mom` — adverse-momentum guard (pre-registered 2026-07-20, NOT yet run;
  DEFERRED behind `sel_null` — see below).**
  Full plan: **docs/AB_PLAN_adverse_momentum.md**. Mechanism, frozen: mirror
  `priced_in_check` — it penalises a move over the shift window that went WITH the signal
  and has no branch for the opposite case, so add one (penalise when a move of the same
  size went AGAINST the proposed direction). **Every constant is inherited, not chosen**:
  window = `SHIFT_WINDOW_DAYS` 14d, threshold = `PRICED_IN_MOVE_PCT` 8.0, penalty =
  `PRICED_IN_PENALTY` 0.2. Motivation is the #15 failure analysis: the 180d `counter_trend`
  guard fired on **0 of 7** shorts while the book's worst trade (QCOM SHORT, conv 0.82,
  −23.9%) carried **+34.9% over the prior 14–20 days** — the danger lives in a window
  neither existing guard watches. Deterministic and post-synthesis, so prompts and
  LLM-cache keys are untouched; flag defaults OFF.
  *Disclosure (rule 1):* the pre-move and outcome of all 30 `monday_t1_pit` trades were
  seen when this was formulated ⇒ no mined window may accept it.
  Staging: **stage 1** — $0 offline replay across all stored grids (a conviction *penalty*
  is exactly replayable from the stored final `conviction`, and can only remove trades);
  reject if the guard fires on <3% of traded rows (the 180d guard's failure mode), misses
  QCOM 05-04 / INTC 04-24, costs >1pp of mean excess on any book, or removes >half of all
  SHORTs. **Stage 2** — acceptance only, ~$4, on Mondays 2026-01-05…04-20 (~15 cohorts no
  sweep has touched), ON vs OFF under `adv_mom_oos`, requiring >+1pp mean 4w excess that
  survives the **cohort-block** CI. Prerequisite: the D1 knowledge-cutoff gate, so
  "unburned" is enforced rather than assumed. If it disappoints, the answer is NOT a
  different threshold — that would be a new pre-registration.
  **Sequencing (changed 2026-07-20, same day, before any run):** `adv_mom` adds ANOTHER
  penalty to the gate, and `sel_null`'s motivating measurement says the existing penalties
  are at best not paying for themselves — so it now starts after `sel_null` resolves, and
  its stage-1 replay becomes exact (rather than approximate) once P2a's `gate_trace` is
  stamped. The formulation itself is untouched and no result has been seen for it, so this
  is a schedule change, not an iteration (rule 3 intact). If `sel_null` shows selection is
  worth nothing, `adv_mom` is moot, not merely delayed.
- **Consensus stage B re-exam (~2026-08+)** — the UNGUARDED #12 block on post-07-17
  forward-covered cohorts; acceptance criteria frozen in #12 detail. B.2 is dead (#13).
- **v3 cutover re-examination (~2026-08-06)** — not a new sweep: `grade_shifts` + report
  once the July semis-wave cohorts mature (the window v3 exists for). Criteria in
  BACKTESTS_2026Q2_PH6_12_13.md §3.

### Formulated, NOT yet pre-registered (each needs its own frozen entry here before any sweep)

- **Catalyst-guard (deterministic risk-gate)** — veto/penalize a NEW direction initiated
  against a >X% short-window realized move (the INTC −40.8 / QCOM −36.8 class; prompt
  version failed — #13 finding 1). Code-level check on stock_prices_daily.
  **Sharpened 2026-07-20 by #15's failure analysis** (docs/FAILURE_MODES_monday_t1_pit.md):
  the existing `counter_trend` guard is not mis-tuned, it is mis-*windowed* — it measures
  180d and fired on **0 of 7** shorts, while the book's worst trade (QCOM SHORT, conv 0.82,
  −23.9%) had 180d = −1.5% but **+34.9% over the prior 20 days**. `priced_in_check` cannot
  cover this either: by construction it only fires when price moved WITH the signal. So the
  formulation is "short-window adverse momentum", the mirror of priced-in.
  **→ PRE-REGISTERED 2026-07-20 as `adv_mom` (see the section above); no longer open.**
- **H-confirm-veto** — directional signal with no `improving` sub-verdict AND no
  confirm-language in the thesis is capped below the trade gate
  (docs/REASONING_MARKERS_2026-07-18.md; QCOM class).
- **H-dip-bar** — contrarian LONG (overreaction cell) requires growth/profitability
  sub-verdict `improving`, else −0.1 conviction (same doc; the 21-trade dip-buying habit).
- **`LT_FEWSHOT` A/B** — graded-analog few-shot in synthesis; wait for a thicker graded
  analog base (~8-10 weeks of live grades).

### Telemetry / maintenance (no behavior change, no registration needed)

- **Fill-convention fix `open_t1` — APPLIED 2026-07-19** (measurement only; decisions,
  labels and prompts byte-identical, LLM cache intact). Entry = open of the first bar
  strictly after as_of (was: last close ≤ as_of — for weekend live cohorts a price printed
  before ~38h of news the decision had read); horizon anchors at entry; benchmarks same;
  stop walk includes the entry-day close; decision-side price reads now use the
  completed-session cutoff (`tools/pit.py` — no-op for weekend/pre-open as_of, PIT-correct
  for future backtest cohorts). All measured books re-graded (6,215 rows; backup + full
  side-by-side in `longterm_agent/reports/regrade_open_t1_2026-07-19.md`). Measured impact:
  Friday backtest cohorts nearly unchanged (all-trades mean excess −0.18pp → +0.03pp;
  per-experiment verdicts #1–#13 all survive, wf_30x_f12 4w traded excess +0.53pp → +0.85pp,
  deep +1.78pp → +2.34pp — the old convention slightly UNDERSTATED them), traded-row mean
  untradable gap ≈ +3bp; `shift_library` mean 4w +4.63% → +3.59% (−104bp) — analog base
  rates were inflated by the untradable day-0/overnight component and now measure the
  tradable quantity; 105 rows whose new exit window hasn't matured were reopened and
  re-grade automatically. Live book had zero graded rows — it starts clean under `open_t1`
  (first grades ≈ 2026-07-21). NOTE: the deployed longterm-agent container still grades
  with the old code — redeploy before the next grading milestone, then re-run
  `python -m longterm_agent.regrade_fill_convention --apply` once (idempotent) to catch
  any rows the old grader touched in between.

- **H-hedged-calibration** — log hedged-share / drama-share per live batch (also the
  mechanism metric for `wf_30x_strong`).
- **Stopped book in forward_report** — show hold + stopped side by side (reporting only).
- **12w horizons** of ids #8-13 mature through ~2026-09-11 — re-run grade (idempotent).
- **PIT audit + fixes 2026-07-19 — APPLIED** (`docs/PIT_AUDIT_2026-07-19.md`;
  `PIPELINE_VERSION` 0.8.0 → 0.9.0). Leakage found and closed in the deep-analysis tool
  path (raw-Cypher gate now enforced in code + `move_*` stripped; intraday `move_1d`
  maturity-masked; `Story.article_count` replaced by an as-of count; knowledge-date
  gating added so an event is only visible once an article reporting it exists) and in
  same-day document granularity (`document_cutoff` / `ingest_cutoff` on every as-of
  reader — this was a POST-ENTRY leak under `open_d0`). Also: `full_signal_backtest.py`
  moved to `open_t1`, analog windows anchored at `entry_date`, STALE-UNGRADED rows now
  printed instead of silently skipped, and a cohort-block bootstrap CI added beside the
  per-trade one (~5× wider on correlated cohorts — the per-trade CIs in every entry above
  are correspondingly too narrow). Detection labels untouched (`DETECTION_VERSION`
  stands); tool/prompt inputs changed, so runs across the bump are not like-for-like.
  Measured on the live graph: the knowledge-date gate alone removes **half** the events a
  30-day window returned (8 → 4 for NVDA at 2026-05-01). Consequences for this ledger:
  #9 (`wf_30x_v0.2_deep`) and #11 ran with those holes open — #9's ACCEPT is to be
  re-checked leak-free (`wf_30x_deep_pit`, demote-only per burned-grid); `monday_t1`
  launched with them open too → treat the running sweep as a contaminated harness check
  and re-run as `monday_t1_pit` under the same frozen criteria (harness-integrity re-run,
  like the `open_t1` regrade — not a variant iteration). Open follow-up: the novelty
  saturation constant is mis-scaled by the article-count unit change (see audit doc).
