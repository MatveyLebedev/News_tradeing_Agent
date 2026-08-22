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
- **`no_overreaction` — close the overreaction cell (pre-registered 2026-08-21, NOT yet run;
  full plan `docs/AB_PLAN_overreaction_cell.md`).** ONE formulation: delete
  `(negative shift, contradicts) → LONG` from `MATRIX_CELLS`, leaving `(positive, confirms)`
  as the only long entry. Flag `LT_CLOSE_OVERREACTION`, default OFF. Motivation is
  mechanism-level, not a delta: the cell exists to buy an overreaction after a name is beaten
  down, and split by the 21-day pre-move it returns +13.83% (median +3.94) when the name came
  in RISING and +2.74% (median **−1.88**, CI covering zero) when it came in falling — it fails
  in exactly the half it was designed for. Evidence is two-sided and disclosed: closing it
  measures +2.03/+3.07/+7.01pp on sweeps at 14/28/84d and lifts the median at all three, and
  +1.70/+2.92pp on the live realised book — but **−2.43pp** on the live open book (the five
  open positions in the cell average +5.77%) and **−2.25pp** on the heavily-mined sweep dates.
  Sign flips between snapshots, which is why it is registered rather than adopted. Stage 1
  replays on old books despite `shift_direction` being unstamped before `75ae71c` (a LONG with
  `contradicts` implies a negative shift once the shipped matrix veto is applied — an
  approximation, labelled as one; live says 7 of 7 carry a negative shift). Acceptance needs
  as_of > 2026-08-21. A3 requires the kept long book's MEDIAN not to fall — the criterion that
  separates this from LONG-only, which raises the mean and drops the median at every horizon
  (−0.43/−1.05/−6.29pp). Power: the cell is 174 of 634 traded sweep decisions (27%) over 30
  dates and 7 of 27 live, ~1.5 per weekly cohort ⇒ 15–20 fresh cohorts, four to five months.
  Must NOT share an arm with `sel_null` or `adv_mom`.
- **Map model sits on a preview channel and is unpinned (logged 2026-08-21, NOT scheduled).**
  `GRAPH_LLM_MODEL` resolves to `google/gemini-3.1-flash-lite-preview`. The stable
  `google/gemini-3.1-flash-lite` exists (OpenRouter, listed 2026-05-07, same 1M context) and
  is ALREADY the default in `core/settings.py:75` — the preview id comes from `.env`, almost
  certainly set before the stable variant shipped (preview 2026-03-03) and never revisited.
  Why it matters beyond tidiness: the map step produces the per-week bucket sentiments that
  `compute_shift_metrics` consumes, so `has_shifted`, `direction` and `shift_score` are
  downstream of it. The detection label is deterministic GIVEN the summaries; the summaries
  are not guaranteed stable on a preview channel. Measured 2026-08-21: three repeat calls on
  five buckets gave a sentiment spread of **0.00**, so there is no evidence of drift today —
  but that is a statement about today, and preview offers no contract about tomorrow.
  **Not scheduled deliberately.** Switching is not a one-line env change: it is a
  label-affecting model swap of the same class as metrics v1→v2, requiring a fresh
  `LT_BUCKET_CACHE_VERSION` (the config says to bump it whenever the corpus definition, map
  prompt or map model changes), a `DETECTION_VERSION` bump, and an old-vs-new label
  comparison on a shared window. Doing it now would split the post-2026-08-21 cohorts that
  `no_overreaction` needs for acceptance straight down the middle — the same way 27 books
  were made unreproducible by an unstamped decision clock. Do it at the next deliberate
  detection-version bump, as one step, not opportunistically. Related and independent: the
  provider pin (`only: novita, allow_fallbacks: false`) is present on 7500 of 21112 stored
  rows and absent on 13612, and applies to `llm_model` only — the map role has a single
  backend and needs none.
- **`confirms_only` — CLOSED 2026-08-21 without running, superseded (pre-registered
  2026-08-09; conformance half shipped as code in `75ae71c`, strategy half restated as
  `no_overreaction` above; full plan `docs/AB_PLAN_matrix_cell.md`; the exploratory
  analysis behind it, including two measurement errors it corrects, is written up in
  `docs/EVENT_STUDY_2026-08-20.md` — that document is hypothesis-generating and cannot
  accept anything).** Mechanism, ONE
  formulation: a LONG/SHORT requires `fundamentals verdict = confirms`; `ignores`, `unknown`
  and `contradicts` are not confirmations and license no direction. Derived from
  `SYNTHESIS_MATRIX_RULES`, not from a window — the event study only located where the code
  departs from the matrix's own text: `LONG·ignores` (n=16), `SHORT·ignores` (n=6),
  `SHORT·contradicts` (n=4) all traded and all lost, plus 25 traded rows with
  `verdict=unknown` and no veto fired (`_has_usable_fundamentals` checks that income data
  exists, not that a verdict landed). Deterministic veto in `risk_gate_node`, flag
  `LT_REQUIRE_CONFIRMS` default OFF, prompts byte-identical, replays offline from
  `gate_trace` at $0. Measured bite before registration: 226 of 560 traded decisions (40%)
  across the ten trace-stamped books with ≥20 traded rows — **203 of 363 LONGs (56%) but only 23 of 197 SHORTs
  (12%)**, i.e. a long-book filter. Disclosure (rule 1): all cell outcomes were inspected
  while formulating, so no mined window can accept it; the narrow `confirms`-only form was
  chosen over a wider `confirms|contradicts` variant precisely to avoid buying a per-side
  carve-out with hindsight, and the cost — closing the matrix's overreaction cell, which
  measured +8.20% on mined windows — is accepted knowingly, that number being post-hoc on a
  burned grid and inadmissible in either direction. Stage 1 is verification only (exactness,
  no side-effects, ≥40% of directional trades retained); a fall in stored-book mean is the
  EXPECTED consequence and is not a rejection. Acceptance needs cohorts with as_of >
  2026-08-09 — there is no usable historical unburned window (news dense only from 2026-03;
  all 32 swept dates run 2026-03-16..2026-07-27). Power: ~2 removed / ~3 kept per weekly
  30-name cohort ⇒ 15–20 fresh cohorts (4–5 months) for a ±1pp read. Risk registered up
  front: the veto is deterministic but keys on an LLM label measured to be config-unstable
  (63 of 567 shift-bearing (ticker, as_of) pairs disagree across configs). Must NOT be run in
  the same arm as `sel_null` — both change what becomes a trade.
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

- **`wf_30x_nonoise` — corpus opinion-filter A/B (pre-registered 2026-07-22, NOT yet run).**
  Hypotheses and full design: `CORPUS_NOISE_EXPERIMENT.md` (H-A). Mechanism: an editorial
  filter over the company corpus — a one-off Stage-2-style classification pass (cheap
  model, batches of 25, headline + first 300 chars, publication-time fields ONLY — PIT-safe
  by construction) into a side table `news_flags (news_id, is_opinion, relevance, tags)`;
  the corpus query in `news_corpus_tool.py` drops `is_opinion = true` rows behind a flag
  (default OFF; prompts stay byte-identical; one classification pass reused across arms,
  never re-run after outcomes are known). Motivation is structural, not window-fitted: an
  external analyst-emulation spec's no-noise rule ("events and official actions, never
  third-party opinions") applied to background profiling. Registered contra-hypothesis
  (genuinely two-sided): narrative shifts surface in commentary BEFORE official events —
  the filter may delay or lose early detections; if filtered-arm-lost detections graded
  well, the filter is rejected. ONE formulation: drop `is_opinion=true` only — no
  relevance-threshold variants, no reclassification, no prompt iteration on the classifier.
  Protocol: the standard grid is burned (rule 1) → it can only REJECT (do-no-harm);
  acceptance needs forward cohorts that postdate this registration. Per #13's noise floor,
  headline deltas within ±1pp are inadmissible; pre-registered mechanism-level predictions:
  (a) map-reduce token cost drops ≈ proportionally to the share of articles removed;
  (b) guardrail — NO_DATA rate does not rise (the <8-article exit must not swallow tickers);
  (c) decision deltas concentrate in tickers whose corpus had high opinion-share (top
      tercile), not uniformly (uniform reshuffle = the #13 noise mode, not a mechanism);
  (d) evals (bucket_map / synthesis_matrix / thesis_quality) not degraded vs `evals/BASELINE.md`.
  Companion hypothesis H-B (event-level dedup + `dup_count`) is registered in
  `CORPUS_NOISE_EXPERIMENT.md` but deliberately NOT queued as a run: per the 2026-07-22
  discussion it is a graph-integration task (Event `SOURCED_FROM` fan-in already dedups;
  the ILIKE/embed recall paths bypass the graph and reintroduce duplicates) — it gets its
  own id, formulation, and frozen criteria if/when picked up.

### HOLDOUT RUN — pre-registered 2026-07-20, BEFORE launch (`holdout_mar_deep_on` / `_off`)

**The last untouched historical data.** Every one of the 21 as_of dates used by the 17
existing books falls on/after 2026-04-24. The news corpus is dense from 2026-03-01 and was
ingested in real time (41 of 6549 March rows arrived >2d late, 1 of 8060 in April), so a
March backtest sees what March saw. C2 probe: gap +8.3pp, under the +10pp bar ⇒ usable,
marginally. **Grid:** Mondays 2026-03-16, 03-23, 03-30, 04-06, 04-13, 04-20 — six cohorts;
the corpus only starts 2026-02-24, so 03-02 and 03-09 lack a full 14d shift window and are
excluded. Same 30 tickers, horizons 14/28, SPY, fill `open_t1`, `LT_METRICS_VERSION=2
LT_LLM_CACHE=true LT_MAX_REFLECTION=0`, graded `--asof-today 2026-07-20`.
**Provider pinned** (`LLM_PROVIDER=novita`, `only`, fallbacks off) — see the provider-pin
entry; without it the two legs could be served at different quantisations.
**Two questions on six cohorts — multiplicity acknowledged.** Running both deep legs answers
deep AND (free, via `gate_trace`) `sel_null` on the same data. Two looks at six cohorts
raises the chance one flatters by luck, so both criteria are frozen here, before launch, and
the second is read at a stricter bar:
- **Q1 deep (primary).** ON beats OFF by >1pp mean 4w `excess_beta` net of D2 costs AND the
  cohort-block CI excludes zero ⇒ deep keeps its place; inside ±1pp ⇒ demote confirmed;
  ON losing by >1pp ⇒ retired outright. This is an INDEPENDENT test of B1's fragile
  verdict (+0.97pp on `excess` / +1.09pp on `excess_beta`), which is why it is worth the data.
- **Q2 `sel_null` (secondary, stricter).** Unburned window ⇒ it CAN accept, but at
  **>+1pp AND the cohort-block CI excluding zero AND net of costs** — not stage 1's
  reject-only bar. Anything weaker is recorded as "survives again", not as acceptance.
- **Both**: with six cohorts a block CI is wide by construction. A result that needs a
  narrow interval to look good is not a result. No variant iteration on this window ever.
**Known limitation, measured BEFORE the run — coverage ramps across the window.** Tickers
clearing `MIN_ARTICLES_FOR_BACKGROUND=8` over the 60d corpus lookback: 14 (03-16), 17, 18,
22, 24, 25 (04-20) of 30, against 25–28 in the burned window. The corpus only begins
2026-02-24, so early cohorts see an empty older half. Consequences, stated so they are not
rediscovered as a finding: (a) **cross-window comparison is invalid** — a March number
against the April–June books would confound edge with a different, attention-selected
universe; (b) **within-window contrasts stay valid** — both deep legs and both `sel_null`
arms see the identical universe, so N shrinks but the contrast is unbiased. Q1/Q2 are
within-window by construction, which is why the run is still worth making.
**RESULT 2026-07-21 — 360/360 runs, 0 errors, 0 provider 404s, 6 cohorts, `gate_trace` on
all 360 rows. Neither question is settled; the window is now spent.**
- **Q1 deep — the frozen rule does not resolve this case, and I will not pick a branch.**
  4w net of costs: ON **+4.95%** (n=79) vs OFF **+2.34%** (n=76) = **+2.61pp**, above the
  1pp bar — but the gap's cohort-paired block CI is **[−0.17, +5.59]pp**, which covers zero.
  The rule required BOTH, and its three branches ("keeps its place" / "inside ±1pp ⇒ demote"
  / "loses by >1pp ⇒ retired") have no case for *large point estimate, interval covering
  zero*. That is a defect in the pre-registration, logged as such. 2w contradicts outright:
  **+0.32pp**. Per-cohort the gap swings −2.74 … +7.81pp.
  *Where it comes from:* not the shared book. Of ON's 79 trades, **18 were taken by ON
  alone and averaged +9.39%**, against 15 OFF-only at +0.35%; the 61 shared trades run
  +3.64% vs +2.84% (3 direction flips). So ~18 trades carry the entire result — the same
  shape as B1, where 3 trades carried it, pointing the other way.
  *Mechanism differs from B1 and that matters:* on the burned grid deep changed direction on
  0 of 55 signals and only filtered 3 out. Here it changed the traded set substantially and
  flipped 3 directions. Deep does not behave the same on the two windows, so neither window
  generalises to the other.
  **Net effect on B1: the demote is NOT confirmed.** The holdout leans the other way without
  reaching the bar. `LT_DEEP_ANALYSIS=false` should not be deployed on this evidence, and
  the earlier recommendation is withdrawn pending the ~2026-08-10 maturation re-read.
- **Q2 `sel_null` — NOT accepted: the two legs disagree in sign.** Cohort-paired delta net
  of costs: on `_off` **+1.13pp**, CI **[+0.26, +2.24]** (excludes zero, would meet the
  strict bar); on `_on` **−0.26pp**, CI **[−1.33, +0.73]**. An estimate whose sign depends
  on an unrelated flag is not an estimate. Also fragile within the passing leg: 3 of its 6
  cohorts show a gap of ~0.00 and the whole effect sits in the other 3. Recorded as
  "survives a sixth time, still unaccepted" — the null has now failed to die on 6 books
  while never once clearing an acceptance bar.
- **Both books are outlier-driven.** ON median **+1.68%** / 54% winners; OFF median
  **−0.90%** / 46% winners. Positive means resting on a minority of large winners, on 6
  cohorts, is exactly the configuration in which a block CI is wide for a real reason.
- **Standing conclusion:** six cohorts cannot settle either question. The remaining honest
  sources are maturation (free, ~2026-08-10) and forward accumulation (~1 cohort/week,
  permanently uncontaminated). No further historical window exists.

**Aborted first launch, 2026-07-20 — no data burned.** The provider pin was applied to every
role, and novita does not serve `gemini-3.1-flash-lite` (only `google-ai-studio` does), so
map calls 404'd and cohorts degraded to NO_DATA. Killed after 18 rows, all NO_DATA, all in
one cohort; rows deleted, so no decision was recorded and the window is intact. Fix: the pin
now applies to `llm_model` only, with a regression test. Logged because a silently degraded
run on irreplaceable cohorts is the most expensive failure available here.

### `rich_synth` — is deep just re-surfacing data? (pre-registered 2026-07-21, BEFORE launch)

Motivated by the structural audit: `synthesis` decides direction and conviction from the
verdict label plus four numbers (both P/E None on 20% of traded rows, `margin_series` on
0%), while `fundamentals_analysis` already saw everything and compressed it away — and
`analysis_tools.py` re-reads `margin_series` directly, so deep may be an expensive path back
to exactly that. **Run:** `holdout_mar_rich_off` — `LT_SYNTH_FUNDAMENTALS=rich` with
`LT_DEEP_ANALYSIS=false`, identical grid/tickers/horizons/provider pin to the two holdout
legs, graded `--asof-today 2026-07-21`. Compared against the STORED legs, not a re-run.
**Burned window ⇒ reject-only. This cannot promote `rich`; it can only rule things out.**
Frozen reading, on 4w `excess_beta` net of D2 costs:
- `rich_off` lands within 1pp of `deep_on` (+4.95%) ⇒ **deep's contribution is data
  surfacing**, obtainable at zero LLM cost ⇒ retire deep, keep `rich`, re-test forward.
- `rich_off` stays within 1pp of `deep_off` (+2.34%) ⇒ **deep does something beyond
  surfacing data**; the prompt fix is not a substitute and `rich` is not the answer.
- In between ⇒ partial, and on 6 cohorts that is *no answer* — record and move on.
Caveat stated in advance: the prompt edit invalidates the content-addressed LLM cache, so
this leg is entirely fresh calls and is NOT cache-comparable to the stored legs. That makes
it a weaker comparison than the deep ON/OFF pair, which ran back-to-back; a difference here
carries the prompt change AND whatever the fresh sampling adds.
**RUN 1 (2026-07-21) — VOID. Not a result: a 14.4% synthesis failure rate.**
It completed cleanly by every signal I had been checking — exit 0, 180/180 decisions, 0
tracebacks, 0 provider 404s, the same 42 NO_DATA rows as both comparators — and read
**+2.43% net (n=59)**, i.e. within 0.08pp of `deep_off` and 2.52pp under `deep_on`, which
under the frozen rule says "deep does something beyond surfacing data". That reading is
withdrawn. **26 of 180 synthesis calls failed after retries** (7 "provider returned error",
~17 "invalid request", 3 a None returned into `.model_dump()`), and each fallback is
`NEUTRAL / conviction 0` — indistinguishable from a decision to stay flat. Comparators:
`deep_on` 1.1%, `deep_off` 1.7%. So the leg's 74 directional signals vs 87, and 59 trades vs
76, are mostly *suppressed calls*, and would have been reported as "the rich prompt is more
selective". The pin (`allow_fallbacks=False`) is what surfaced the provider flakiness as
errors rather than silent rerouting — working as intended, but the pipeline then buried it.
**Fix landed:** the fallback now carries `synthesis_failed` + `synthesis_error`;
`backtest_metrics.synthesis_failed/failure_rate` also detect legacy rows by their sentinel
thesis; `ab_selection` prints the rate and refuses to let a >5% book be read against another.
**Audit of every stored book:** `wf_30x_f12_v3` 0.7%, `wf_2026q2_v0.1` 1.4%, holdout legs
1.1%/1.7% — all low and, for the deep pair, symmetric (2 vs 3 rows), so **Q1 and Q2 stand**.
Re-run needed before `rich_synth` has any reading at all.
**RUN 2 (2026-07-22) — healthy (1.7% failures) and the frozen reading resolves:**
4w net excess_beta `rich_off` **+2.01%** (n=69) — within 0.33pp of `deep_off` (+2.34%) and
2.94pp under `deep_on` (+4.95%). Frozen branch: **deep does something beyond surfacing
data; the prompt fix is not a substitute.** (Burned window ⇒ this REJECTS "deep is just
data plumbing" here; it does not settle what deep's something is.)
- *The decisive detail — INTC 2026-04-06:* the rich prompt contained the exact margin
  numbers deep had cited (15% → 38.2%, first positive operating margin), and the model
  still said NEUTRAL. The data alone did not move it; deep's narrative block did. Whatever
  deep buys, it is not (only) field access.
- *The rich prompt itself is value-neutral:* vs the thin book on the same window
  (both deep-off), −0.33pp — noise. But it is NOT behaviour-neutral: 26 of 180 directions
  changed, 9 trades in (+4.91%), 16 out (+4.61%) — a large, value-neutral reshuffle.
- *Meta-finding, third confirmation:* any input-text perturbation (deep block: 29 flips;
  rich fields: 26 flips; one uncached map bucket: PEP flip) reshuffles decisions at the
  same scale as the effects being measured. Book-level differences of ~1pp between
  variants are mostly reshuffle noise, which is why cohort-block CIs on 6 cohorts keep
  covering zero and why acceptance bars here stay conservative.
**Action: `LT_SYNTH_FUNDAMENTALS` stays `thin` (default unchanged); `rich` is not pursued.
Deep stays ON (status quo); its window-dependent value remains the open question, next
informed by the free ~2026-08-10 maturation.**

### `gap_guard` — skip entries the market already made (pre-registered 2026-07-22, BEFORE
any measurement)

Motivation (mechanism, not mining): the agent never sees a post-Friday price — no futures,
no premarket — so at entry it cannot know whether the open already absorbed the news it is
trading. `priced_in_check` measures through the prior close and is structurally blind to
the entry gap. The user's original concern from day one ("цена открытия может частично
отыграть прогнозируемый тренд"), now as a deterministic rule.
**Formulation, ONE, frozen before looking:** at entry, compute
`gap = entry_open / prior_trading_close − 1`, signed into the signal's direction
(gap for LONG, −gap for SHORT). **Block the entry if signed gap > +2%.** The 2% is a
prior (≈ one daily sigma of a mega-cap), chosen before any measurement; no other
threshold will be read, reported, or "sensitivity-checked" — a threshold sweep on stored
books is mining with extra steps.
Deterministic and post-decision ⇒ both arms replay offline from stored rows (entry_px is
stamped; prior close is in daily bars) — same construction as `sel_null`.
**Evidence rules:** stored books are burned ⇒ diagnostic / reject-only. Acceptance only on
forward cohorts (`monday_t1_fwd` and successors), criteria as elsewhere: >+1pp net on the
kept book vs unguarded, cohort-block CI excluding zero. Rejected if blocked trades turn
out BETTER than kept by >1pp (the guard would be throwing away the edge).
**VERDICT (same day, minutes later): REJECTED by its own criterion — and inverted.**
Across the five PIT-clean books, deduped to unique (ticker, direction, entry_date):
154 trades, the guard blocks **7**, and the blocked are the BEST trades in the set —
mean **+24.4%** / median **+7.98%**, 5/7 winners, vs **+1.69% / +0.77%** kept. Without
both INTC entries (+84.3%, +56.7%) the blocked still run +6.0% / +2.8%. The block list is
the AI-capex rally roll: INTC ×2, ORCL ×2, AVGO, LLY, NVDA. An opening gap INTO the
signal read as continuation, not exhaustion — on this tape, "the move already happened"
was where the move was still happening.
Caveats that cap the claim: n=7 blocked; every one is a LONG in a single rising-tape
regime (Mar–Jun 2026); a down-tape could invert it back. So this REJECTS the guard as
formulated (it would have cut the edge, decisively) but does NOT establish the opposite.
The inverse hypothesis — gap-with-signal as a positive selection feature — is noted here
as data-born and is NOT registered; if ever pursued, forward cohorts only.
Consequence upstream: the midday-run proposal's premise ("the open eats the move") is
empirically backwards on our books, which also retroactively rationalises why the
Monday-06:30 regime showed no edge over Sunday.

`longterm_agent/costs.py`, per the audit's spec: flat spread per side + borrow on SHORTs,
printed as a third book. Frozen defaults: 5bp/side stock, 1bp/side hedge, 50bp/yr borrow
pro-rated by days held ⇒ **12bp round trip on a long, ~16bp on a 28d short**. The hedge leg
is charged because `excess` is a pair (stock vs SPY), not a bare stock return. `stress()`
is a 4× preset for sensitivity only, never for a verdict.
| book (4w, beta-adj) | gross | net | net @ stress×4 |
|---|---|---|---|
| `wf_30x_deep_pit_on` | +2.31% | +2.18% | +1.75% |
| `wf_30x_deep_pit_off` | +1.22% | +1.09% | +0.66% |
| `monday_t1_pit` | +0.50% | +0.37% | **−0.03%** |
**The audit's warning is confirmed on the leak-free book:** `monday_t1_pit`'s entire edge
sits inside the cost band once costs are pessimistic. That, not arm-differentiation, is
what D2 buys.
**CORRECTION — D2 is NOT a blocking dependency of `sel_null`, and my stated reason for
making it one was wrong.** The ledger claimed "the challenger raises turnover ~40%, so
costs must be netted first". Measured: costs move the incumbent −13bp and the challenger
−13bp, i.e. the N1 delta is unchanged to four decimals (+0.0085 → +0.0086). The challenger
raises **breadth** (55 positions vs 47), not **frequency** — both arms hold 28 days, so at
fixed capital split equally the cost per dollar is identical. Breadth and turnover are
different things and I conflated them. `sel_null` stage 2 is therefore unblocked; D2 stays
because it changes every book's absolute level, which is decision-relevant on its own.

### C2 — does the model RECALL the holdout? (probe run 2026-07-20)

`longterm_agent/knowledge_probe.py`. The data-side canaries prove no reader returns
post-decision data; they say nothing about the checkpoint's memory. We call
`deepseek/deepseek-v4-pro` **unpinned**, so OpenRouter serves whatever is deployed — per the
operator, the GA builds rolling out as of July 2026 carry an **April 2026** cutoff, which
would put a March 2026 holdout INSIDE the training window (and the current 04-24…06-19 grid
outside it — i.e. the holdout is the riskier window, not the safer one).
Design: difference-in-difference, never absolute accuracy — equities drift up, so an
absolute hit rate proves nothing. Same 10 mega-caps, no news/fundamentals/prices in the
prompt, 6 Mondays inside the claimed cutoff (treatment) vs 6 after it (control).
**Run 1 — VOID (harness bug, not a result).** `max_tokens=16` against a reasoning model
returned empty content on 73% of calls; the survivors were those answered without
deliberation, i.e. selected for confident recall — a selection that manufactures the very
gap being tested. It read +35.7pp. Recorded so the re-run is not mistaken for a second
attempt at a disliked answer: the re-run was forced by missing data, and the criterion was
never touched. The probe now aborts above 5% unparsed.
**Run 2 (max_tokens=2048, 60/60 both arms, zero dropped):** treatment **61.7%**
(p=0.045 vs a coin), control **53.3%** (p=0.348), **gap +8.3pp** — under the pre-registered
+10pp bar ⇒ **USABLE**. Read it as a marginal pass, not a clean one: the treatment CI is
[50.0%, 75.0%], touching chance, and n=60 per arm. No strong evidence of recall; not proof
of its absence.
*Consequence beyond C2:* the unpinned model ID is now the leading explanation for the
measured replay drift (10 cache misses, conviction 0.82 → 0.78 on an identical corpus),
displacing graph maintenance — a preview→GA rollover would produce exactly that. **Pin the
model** before any further A/B, or every future comparison inherits this confound.

### `monday_t1_fwd` — forward continuation of the Monday-morning regime (registered 2026-07-22)

Operator judgment call, stated as such: the Monday question (freshest news, smallest
decision→entry gap — a median 52 articles land in it vs 205 for the live Sunday schedule
and 514 for the Friday backtest grid) is worth an answer, `monday_t1_pit` is inconclusive
on 3 matured cohorts, and **forward cohorts are the only unburned data that will ever
exist again**. One cohort per week: the SAME 30 tickers, Mondays `--at 06:30 --fill d0`,
horizons 14/28/84, deep on, `LT_METRICS_VERSION=2 LT_LLM_CACHE=true LT_MAX_REFLECTION=0`,
provider pinned `novita` (failure rate is now measured per-row; a >5% cohort gets re-run,
not silently kept). Each cohort is run BEFORE any of its outcomes exist — 07-20's 2w
matures 08-03 — so this is pre-registered forward testing, not backtesting.
**Criteria: none of their own.** Cohorts accumulate into the SAME frozen monday_t1 read
(±1pp band, cohort-block CI, day-1 paired value) at the ~2026-08-10 re-read and after;
no new formulation, no variant iteration. The book is separate from `monday_t1_pit` so
the registered historical grid stays exactly as registered.
Live stays on Sunday until the Monday read clears its frozen bar — the schedule follows
the evidence, not the other way around.
**Cohort 2026-07-20 (run 2026-07-22) — accepted at 3.3% failures, after a lesson.**
Run 1: 23% synthesis failures → voided per the rule above. The retry budget was then
widened (2×~3s → 4×~15s exponential, and a None result now counts as a failure —
`core/llm.py`), and the cohort re-run. **The re-run REPLACED the decisions**: identical
corpus (same 140 articles, same buckets, provider pinned both times, 1.5h apart), yet one
map bucket that had failed uncached in run 1 came back fresh in run 2 → a slightly
different bucket sentiment → a different profile text → a synthesis cache MISS → PEP
flipped SHORT 0.55/traded → NEUTRAL. **This is the replay-drift mechanism, finally
measured end-to-end: transient call failures leave cache-coverage holes, and any upstream
text difference cascades into downstream cache misses.** Not graph maintenance (corpus
identical), not a checkpoint change (one build), not quantisation (pinned).
**Protocol amendment, effective immediately: a cohort is NEVER re-rolled.** A re-run is a
different experiment wearing the same date. The first run that clears the failure
threshold stands; rows that failed are patched INDIVIDUALLY (`--tickers <failed>` only),
which preserves every healthy decision and its cache. Applied here: INTC patched clean;
WMT returns an empty/refused completion deterministically (2 runs × 5 attempts) — a
persistent per-prompt provider refusal, not a transient; it stays as a marked
`synthesis_failed` row. Final book: 30 rows, 1 failure (3.3%), 0 directional, 0 traded.
Also the strongest argument yet for running forward cohorts ON their Monday by schedule:
every day between as_of and the run adds drift surface.
**CLOSED 2026-07-22 — operator decision, recorded as such.** Same day the scheduler job
was built (and before it ever fired), the operator closed the Monday-morning line:
"monday is the wrong way, we test intraday later instead." This is judgment, not data —
the Monday hypothesis dies UNPROVEN, not disproven, and that distinction is preserved
here on purpose. What remains: the one forward cohort (07-20) stays in the book; the 12
`monday_t1_pit` cohorts mature regardless, so the free ~2026-08-10 read still happens and
will be reported — as the closing entry for this line, not as grounds to reopen it
(reopening is a registered decision; `LT_MONDAY_FWD` defaults off and is pinned off by
test). The successor direction is `midday_mon` below.

### Aux horizons 6w/8w — measured 2026-07-22, DIAGNOSTIC ONLY (declared before looking)

Motivation is mechanical, not mined: the agent's own theses state horizons of "6-12
weeks" / "8-16 weeks", while every verdict in this ledger reads the 4w book — we grade
shorter than the agent claims to think. Measuring 42d/56d on stored decisions costs $0
(pure price lookups; decisions untouched).
**Status frozen in advance: these numbers can never be a verdict.** Reading five horizons
and citing the best is horizon-shopping; the ONLY registered reading is the monotonicity
question — does the edge grow from 2w→4w→6w→8w toward the agent's stated horizon, or
decay? No threshold, no acceptance, no "6w works better" conclusion from burned books. If
a longer horizon is ever to become primary, that is a pre-registered change ahead of
forward cohorts, with D2 borrow costs rescaled.
**Measured (2026-07-22), net excess_beta.** The only composition-clean read is the holdout
pair — 100% of trades matured at every horizon:
| | 2w | 4w | 6w | 8w | 12w |
|---|---|---|---|---|---|
| `holdout_on` (79/79) | +1.54% | +4.95% | +5.57% | **+6.18%** | +5.36% |
| `holdout_off` (76/76) | +1.23% | +2.34% | +2.61% | **+4.12%** | +1.75% |
A hump peaking at ~8w in BOTH legs, rolling off by 12w — consistent with the agent's own
stated "6-12 weeks" thesis horizon, front-half weighted. Medians strengthen with horizon
too (`_off` flips −0.90% → +2.86% at 8w; winners 46% → 59%), so the longer read is not
just fatter tails. The registered monotonicity answer: **the edge does not decay past 4w;
4w grading likely cuts the signal mid-growth.**
*The survivor trap, documented because the numbers look better than the clean ones:*
`pit_on` 12w reads +5.00% at 36% matured and `monday_t1_pit` 12w reads +5.84% at 50%
matured — those subsets are exactly the early cohorts (04-24, 05-01, 04-27), the best
weeks of the whole period. On full books 12w is WORSE than 8w. The earlier warning on
monday-12w ("n=15 in ONE cohort — an ESTIMATE, not evidence") measured out correct.
*Forward action (no registration needed — measurement only):* add `42 56` to the graded
horizons of `monday_t1_fwd` and subsequent books so the 2w→12w curve accumulates natively
on unburned cohorts; any primary-horizon change remains a pre-registered decision on that
forward data.

### `sunday_sim` — the last two Sundays under the CURRENT code (registered 2026-07-22,
BEFORE launch; outcomes do not exist yet for either cohort)

Two cohorts at the live regime's own stamp (Sunday 06:00 ET, fill `open_t1` → Monday
open), the live 64-ticker universe (taken from the 2026-07-19 live rows), horizons
14/28/42/56/84, deep on, provider pinned, cache on.
- **2026-07-12** — the Sunday nothing ran (first live batch was a week later). A clean
  pre-outcome cohort (2w matures 07-27) that also fills the gap in the Sunday series.
- **2026-07-19** — live DID run, on pipeline 0.8.0, i.e. BEFORE the PIT fixes. The
  simulation is the current 0.9.x code on the same as_of: the question is **does today's
  code stand behind the six open positions** (they mature 08-16), not attribution — the
  delta vs the live rows mixes code fixes with drift, and is read only as
  agree/disagree per position.
**Honest limit, stated up front:** a run 3–10 days after its as_of does not reconstruct
what a live run would have decided (drift is measured); these are PIT-clean cohorts of
TODAY'S code. Failure protocol as everywhere: >5% synthesis failures ⇒ patch tickers
individually, never re-roll.

**Run result (2026-07-22, git 2147271, provider pinned novita, single provenance stamp).**
Health: 07-12 → 2/64 synthesis failures (3.1%), 07-19 → 3/64 (4.7%); both under the 5%
bar, the run stands. NO_DATA (thin news coverage, a different category from failure):
6 and 5 tickers respectively — INTU/PG/TMUS/TXN in both weeks, i.e. persistent coverage
holes in the 64-universe, not transient. None of the failures touched the six live
positions — the comparison below is fully covered.

**07-19: does today's code stand behind the live book?** Per position (live 0.8.0 →
sim 0.9.x):

| live position | sim decision | read |
|---|---|---|
| JNJ LONG | LONG 0.80, traded | **agree** |
| MRK SHORT | SHORT 0.80, traded | **agree** |
| NKE SHORT | SHORT 0.80, traded | **agree** |
| NFLX SHORT | SHORT 0.50, NOT traded | direction agrees, gate does not |
| AMD LONG | SHORT 0.25, not traded | **direction flipped** (weak) |
| BRK-B LONG | SHORT 0.65, traded | **direction flipped, traded opposite** |

3 of 6 fully confirmed, NFLX confirmed in direction only. The two flips are both
live-LONGs turning sim-SHORT; BRK-B is the substantive one (0.65, would trade the other
way), AMD's is weak (0.25, untraded) — and AMD is currently the live book's best
position (+9% in 3 days), a reminder that agree/disagree is not a quality score.
The sim would also open AVGO LONG 0.55, BA LONG 0.70, ORCL LONG 0.62 — trades the live
0.8.0 code did not take. Read per the registration: watch BRK-B (and NFLX's gate) until
08-16 maturation; no action on open positions from a simulation.

**07-12 (the gap-filling Sunday cohort):** traded GOOG LONG 0.65, MRK SHORT 0.80;
directional-untraded AMD SHORT 0.35, AVGO LONG 0.50, INTC LONG 0.50. Entry Monday
07-13 open; 2w matures 07-27, 4w 08-10, 12w 10-05. Note MRK SHORT 0.80 fires on BOTH
Sundays — the one signal in this exercise with any persistence claim.

### `wf_fresh30_pit` — current config on tickers it was NOT tuned on (pre-registered
2026-07-23, BEFORE launch)

**The question, frozen before looking:** the +result on `wf_30x_deep_pit_on` is in-sample
by ticker — those 30 names are the set every variant was iterated on. Does the current
config's daily excess-beta curve REPLICATE on 29 liquid large-caps outside that set, on
the SAME 9 cohort dates (2026-04-24 … 2026-06-19, weekly), same config (deep on, PIT,
metrics v2, provider pinned novita, horizons 14/28/42/56/84, fill t1)? A paired-by-date,
different-universe read isolates ticker-set generalization from everything else.

**Universe (29, none in the wf_30x 30; PM dropped — zero price/fundamentals in DB):**
JNJ UNH MRK ABBV TMO · GE BA CAT HON UPS RTX · COST PG MCD · HD LOW NKE BKNG · T TMUS VZ ·
CSCO IBM NOW ACN · CVX NEE · WFC AXP. All verified before launch: full daily bars since
2024-06 (beta estimable) through 2026-07-21, 89–111 fundamentals filings each.

**Gradability at launch (prices through 2026-07-21):** 2w and 4w fully matured (9/9
cohorts) — that is the registered primary comparison, paired against wf_30x_deep_pit_on
at the same horizons. 6w 7/9, 8w 5/9, 12w 1/9 — the long end accrues into Sept and is
read later, never re-run. NO_DATA is expected (thin-coverage names) and is data, not
failure; >5% synthesis failures ⇒ patch tickers individually, never re-roll.

**The ONLY registered reading:** does the 2w→4w edge hold on the fresh set (sign and
rough magnitude), or collapse? A collapse says the in-sample books were ticker-overfit;
replication says the config generalizes. No horizon-shopping, no "fresh set did better"
conclusion — the burned-grid rule applies (this can corroborate or reject, not tune).

**RESULT (2026-07-23, run complete; health OK: 3.6% synthesis failures < 5%, 23 NO_DATA,
40 traded of 247). The edge does NOT replicate — it collapses.** Registered horizons, FRESH
vs in-sample (wf_30x_deep_pit_on), median / mean excess-beta:

| horizon | FRESH (29 untuned) | in-sample (30 tuned) |
|---|---|---|
| 2w (n=40) | **−0.57% / +0.92%** | +0.92% / +2.18% |
| 4w (n=40) | **−0.04% / +0.40%** | +2.97% / +2.31% |
| 6w (n=33, 7/9 matured) | **−2.61% / −1.04%** | — |
| 8w (n=28, 5/9 matured) | **−3.64% / −1.32%** | — |

The daily curve (from price paths) drifts from ~0 to −4…−6% by day 56–72 — a mirror of
the in-sample/holdout climb to +2…+7%. The run's own report: "0 survive BH FDR 5% — no
per-ticker edge survives correction". Reading the ticker dimension against the date
dimension already on file: in-sample tuned tickers +2.97% (4w) → holdout SAME tickers,
held-out DATES +1.80% → FRESH untuned tickers −0.04%. The **ticker** axis moves the result
far more than the **date** axis: the measured edge is concentrated in the specific names
the config was iterated on.

**Honest confounds (do not let them explain the result away, but state them):** the fresh
set skews old-economy (healthcare/industrials/staples) vs the in-sample mega-cap
tech/financials, and carries thinner news (23 NO_DATA). So "ticker-overfit" and "works only
on high-coverage mega-caps" are both live readings — and BOTH lower the expectation for the
broad live universe (64 names, closer to FRESH than to the tuned 30). n is small (40) and
6w/8w are partially matured; but the sign is negative and consistent across every horizon,
so the small-n caveat does not rescue the edge. **This is a reject, not a tune:** it does
not change any knob; it recalibrates what the live book should be expected to earn (near
zero at matured horizons on names outside the tuned set), and raises universe breadth
(C1) from "nice to have" to "the honest test of whether anything here generalizes".

**Sub-analysis (2026-07-23, disentangling the two confounds named above; 4w, n=40):**
- **Coverage hypothesis REJECTED.** Split at the median corpus size (34 articles): high-
  coverage trades med **−2.65%** (n=18, win 39%) vs low-coverage med **+0.42%** (n=17,
  win 53%). More news did NOT recover the edge — it did slightly worse. The "works only on
  high-coverage mega-caps" reading is off the table; the fresh failure is not a data-volume
  artifact.
- **Sector concentration (hint, NOT a verdict — n per sector is 2–15).** Positive buckets:
  Tech +2.94% (n5, mean skewed +9.1% by one outlier), Health +1.77% (n6), Energy +4.43%
  (n3) — the sectors that most resemble the in-sample mega-cap tech/pharma. The drag sits
  in Industrials −4.17% (n15, the largest bucket), Comm −5.12% (n4), Staples −3.09% (n3).
  Consistent with the edge being tied to the KIND of name the config was iterated on rather
  than to coverage — but per-sector samples are too thin to assert it. What survives
  cleanly: coverage does not explain the collapse; the edge is name/sector-specific, and
  the broad live universe spans exactly the sectors where it failed here.

**Sector-matched comparison (2026-07-23) — REFINES "ticker-overfit" to "sector/style
generalization".** Same 4w median, in-sample vs fresh, per sector present in both:

| sector | in-sample | fresh | read |
|---|---|---|---|
| Tech | +2.98% (n16) | +2.94% (n5) | **replicates cleanly** — new tech names (CSCO/IBM/NOW/ACN) behave like tuned tech |
| Health | +8.97% (n5) | +1.77% (n6) | same sign, weaker |
| Fin | +2.07% (n8) | +0.63% (n2) | same sign, weaker |
| Staples | −2.96% (n5) | −3.09% (n3) | negative in BOTH (consistently no edge) |
| Comm | +4.67% (n5) | −5.12% (n4) | flips — but "Comm" = internet/media in-sample (GOOGL/META/NFLX) vs telecom fresh (T/TMUS/VZ); different sub-industry, not a clean match |
| Discr | +4.75% (n4) | −1.55% (n2) | flips — AMZN/TSLA/UBER growth vs HD/LOW/NKE retail; also different style |
| Industrials | — (never trained) | −4.17% (n15) | the largest fresh bucket and biggest drag; no in-sample counterpart |
| Energy | −5.80% (n1) | +4.43% (n3) | in-sample n=1 is noise |

**Revised verdict:** it is NOT pure ticker memorization — where the sector genuinely
matches (Tech, cleanly; Health/Fin in sign), the edge REPLICATES on names never seen. The
overall fresh collapse is driven by (a) Industrials (n15, a style the config was never
tuned on — the single biggest drag) and (b) "sector" labels hiding different sub-industries
(telecom ≠ internet). So the honest statement is a **style/sector generalization limit**:
the method carries within the growth/tech/pharma names it was built around and does not
transfer to industrials/telecom/utilities. Caveat stands — per-sector n is 1–16, Tech
(n5 vs n16) is the only solid cell. Implication is unchanged for the broad live universe
(it spans the non-transferring sectors) but more hopeful for a tech/pharma-weighted book,
and it reframes C1 as "expand WITHIN the sectors that transfer, and measure per-sector,"
not "add breadth blindly."

**ROOT CAUSE (2026-07-23) — it is the SHORT book, not the sectors.** Splitting 4w by
direction cuts cleaner than any sector split:

| | FRESH | in-sample |
|---|---|---|
| LONG | +1.60% med, win 60% (n25) | +2.93% med, win 62% (n29) |
| SHORT | **−6.36% med, win 33% (n15)** | +3.13% med, win 73% (n15) |

The LONG side GENERALIZES (fresh +1.6% vs in-sample +2.9%, both positive, same order). The
collapse is entirely the SHORT side: in-sample shorts won 73% (+3.13%), fresh shorts won
33% (−6.36%). The sector story was a projection of this — the losing shorts happened to
cluster in industrials/telecom.

**Mechanism (from the losing theses):** the worst trades are all shorts from the
"confirmation quadrant" — *negative news shift + YoY margin compression → high-conviction
SHORT* (GE −19.8%, NOW −11.9%, HON −9.9% @conv 0.85, UPS −10.8%). The agent shorted quality
names in uptrends (GE Aerospace on a rally, NOW at +22% revenue); margin compression is a
slow, well-known signal and price momentum ran the shorts over — several theses literally
say "the stock rallied". The LONG winners are the mirror: buy the macro-overreaction dip on
improving fundamentals (CSCO +27%/+28%, ABBV +14%, RTX +11%). This is the SAME error
`gap_guard` already proved (momentum is continuation, not exhaustion): the short logic
fires against momentum. In-sample shorts' 73% win rate now looks like the overfit — a
fragile short edge that held only on the tuned names. **Actionable, but a pre-registered
change, not a live edit:** candidate hypotheses to test next are (a) a momentum guard on
shorts (no short into a positive trailing-return / uptrend — the gap_guard inversion applied
to the short gate), (b) require shorts to carry negative price momentum, not just
deteriorating margins, or (c) LONG-only. Each needs its own frozen registration on forward
cohorts before any knob moves.

**Stop-loss check (all numbers above are the HOLD book, no stop).** Re-running fresh with
the 10% stop (stopped book): SHORT −6.36% → −6.12% (5/15 hit stop) — the stop does NOT
rescue the short book, the root-cause holds. And it HURTS fresh LONGs: +1.60% → −1.82%
(whipsaw — the overreaction-dip longs need room to recover and the stop cuts them on the
temporary drawdown). In-sample longs never breached (0 stops), so the 10% level was itself
tuned where it was harmless; on out-of-sample names it is a drag, not a guard. Net: the
stop is not the short-book fix, and it is a second knob (like the short gate) that needs
re-calibration on forward data.

### `wf_fresh30_holdout` — the double-out-of-sample cell (pre-registered 2026-07-23,
BEFORE launch)

Completes the 2×2: fresh tickers had only been run on the tuned Apr–Jun dates
(`wf_fresh30_pit`); this runs the SAME 29 fresh tickers on the held-out MARCH dates
(6 weekly cohorts 2026-03-16 … 2026-04-20, the `holdout_mar_deep_on` window) — names the
method never saw, on dates it never saw. Same config (deep on, PIT, metrics v2, novita,
horizons 14/28/42/56/84, fill t1). All horizons already matured (6/6, prices through
2026-07-21) — the full curve grades immediately, unlike wf_fresh30_pit.

|  | tuned dates (Apr–Jun) | held-out dates (Mar) |
|---|---|---|
| tuned tickers | wf_30x_deep_pit_on | holdout_mar_deep_on |
| fresh tickers | wf_fresh30_pit | **wf_fresh30_holdout (this)** |

**Registered prediction (falsifiable, frozen before looking):** if the wf_fresh30_pit
finding is robust to period, this run shows LONG median > 0 and SHORT median < 0 at 4w —
the LONG book generalizes across both ticker AND date, the SHORT book stays broken. The
falsifier: if SHORT ≥ 0 or LONG ≤ 0 here, the Apr–Jun direction split was period-specific,
not a property of the method. Reads: vs holdout_mar_deep_on (tuned tickers, same dates →
isolates ticker effect on held-out dates) and vs wf_fresh30_pit (same tickers, other dates
→ isolates date effect). Burned-grid rule: corroborate or reject, never tune. NO_DATA
expected; >5% synthesis failures ⇒ patch individually.

**RESULT (2026-07-23) — PREDICTION FALSIFIED. The direction split is period-specific.**
Health: 1/174 synth failure (0.6%), but **112/174 NO_DATA (65%)** — the fresh (old-economy)
names have very thin March news coverage, so only 13 traded. At 4w:
- LONG: n=9, win 22%, median **−8.78%** (RTX −18%, IBM −17%, WFC −12%, MCD ×2 crushed)
- SHORT: n=4, win 100%, median **+12.45%** (NKE/LOW/HD/IBM — retail/discretionary fell)

Both falsifier conditions met (LONG ≤ 0 AND SHORT ≥ 0). Per pre-registration I do NOT
explain this away with the small n — the "LONG works / SHORT broken" split from
wf_fresh30_pit did NOT survive to a held-out period; it flipped. **Pooling both OOS periods
(the honest aggregate), 4w by direction: LONG n=34 median −0.04% (win 50%), SHORT n=19
median −2.21% (win 47%) — both ≈ zero.** The Apr–Jun direction split washed out.

**What this corrects:** the earlier `wf_fresh30_pit` "ROOT CAUSE — the SHORT book" entry
was itself overfit to one period. The double-OOS test caught it. The actionable candidates
that hung off it (momentum-guard-on-shorts, LONG-only) are therefore NOT supported — they
were built on a period-specific artifact. **The single robust conclusion across every OOS
cut: on names and dates outside the tuned set, no edge is demonstrated in either direction
(pooled ≈ 0).** The in-sample and single-period edges are period/ticker-specific. Caveat
both ways: holdout n=13 (65% NO_DATA) is too thin to assert shorts "work" either — the
point is the split is unstable, not that its opposite is true. Live-book expectation stays
at ≈ zero; the only thing that will move this is real forward out-of-sample (sunday_sim +
the live positions), not more historical mining.

### `wf_extra13` — the live-universe names never yet backtested (pre-registered
2026-07-23, BEFORE launch)

The live 64-ticker universe = the 30 tuned + 21 of the fresh-29 + **13 that no backtest
has ever touched**: ABNB AMAT BRK-B CMCSA COP GOOG INTU LMT MU PANW PYPL SBUX TXN. This
runs the current config on those 13 so no live name is completely unmeasured. All verified
for price + fundamentals before launch. (Caveat: GOOG is Alphabet class C — economically
the same issuer as the in-sample GOOGL, so it is not a truly independent name; flagged, not
dropped.)

**Design learns from wf_fresh30_holdout:** a single-period read is period-specific and
misleads (that run falsified its own prediction). So this runs BOTH OOS windows at once —
14 weekly cohorts 2026-03-16 … 2026-06-19 (spanning the March holdout window and the
Apr–Jun window continuously), same config (deep on, PIT, metrics v2, novita, horizons
14/28/42/56/84, fill t1). The reading is the POOLED result, never a single cohort.

**Registered reading (frozen):** pooled across all 14 cohorts, 4w by direction — does
either LONG or SHORT show a non-zero edge on these 13 previously-unmeasured live names?
Prediction, consistent with the pooled fresh result (both directions ≈ 0): ≈ 0 both ways.
This run mainly ADDS OOS names to the pool; the question is whether "no demonstrated OOS
edge" survives more names. Burned-grid rule: corroborate or reject, never tune. NO_DATA
expected; >5% synthesis failures ⇒ patch individually.

**RESULT (2026-07-23).** Health: 4/182 synth failures (2.2%), 52 NO_DATA (29% overall;
65% in the March cohorts, ~15% in Apr–Jun — coverage is thin going back in time). 26
traded. wf_extra13 alone (4w): LONG +7.94% (n17, win 65%), SHORT +10.11% (n9, win 78%) —
came in strongly positive BOTH ways, the opposite of the ≈0 prediction.

**This forces an honest re-read of the whole OOS pool — and the answer is "underpowered,
cannot tell."** Three OOS runs, pooled trade mean 4w: wf_fresh30_pit +0.40%,
wf_fresh30_holdout −0.60%, wf_extra13 +11.81% — the run-to-run spread is enormous and the
entire positive tilt rests on one hot run (drop extra13 → +0.16%). Pooling all 79 OOS
trades: mean +3.99%, median +1.75%, win 56%. The naive trade-level 95% CI [+0.68, +7.31]
"excludes 0" — but that is FALSELY narrow (trades within a cohort are correlated). The
honest cohort-block CI (23 cohorts): mean +3.88%, **95% CI [−0.13, +7.89] — INCLUDES 0.**

**Verdict: no OOS edge is established, and none is ruled out.** The point estimate is
positive (~+4% cohort-mean) but not distinguishable from zero once correlation is handled,
and it swings from ~0 to +12% depending on which run you weight. This supersedes the
earlier "pooled ≈ 0" (that was 2 runs) AND the "clear edge" the naive CI suggested — both
were sampling noise. Note extra13's names (ABNB, AMAT, MU, PANW, PYPL, TXN, INTU, BRK-B,
GOOG…) are mega-cap tech/growth, closer to the in-sample style than the old-economy
fresh-30 — consistent with "growth/tech transfers" but far too thin to assert. **The
ceiling of historical mining is reached: only forward data (sunday_sim + the live book,
maturing from 2026-08-10) can move this.** No knob changes on any of it.

### `wf_30x_strong_sonnet` — does a stronger JUDGMENT model help? (pre-registered
2026-07-23, BEFORE launch)

**Frozen question:** upgrading ONLY the three judgment-heavy call sites (synthesis
LongTermSignal, fundamentals verdict FundamentalsTrend, deep-analysis reduce
DeepAnalysisOutput) from deepseek-v4-pro to `anthropic/claude-sonnet-5`, holding
EVERYTHING upstream identical — does trade excess-beta improve? Detection labels are
untouched (they come from MAP_MODEL sentiments + deterministic metrics), so this isolates
"better reasoning on the same signals," not a different signal.

**Design — cache ON, single treatment arm, paired to the existing baseline.** Frame =
`wf_30x_deep_pit_on` (30 tickers × 9 weekly cohorts 2026-04-24…2026-06-19, horizons
14/28/42/56/84, fill t1). With the LLM cache ON, only the Sonnet strong calls miss (new
model → new cache key) and run fresh; the corpus, map/detection, deep-analysis map threads,
reflection all HIT the baseline cache and are reused byte-for-byte. So the upstream is
identical to `wf_30x_deep_pit_on` and any outcome difference is purely the strong model —
no map-drift confound. STRONG_LLM_MODEL only; llm_model=deepseek-v4-pro,
MAP_MODEL=gemini-3.1-flash-lite, detection 0.2.0, prompts, gate=llm, deep on, costs all
frozen.

**Noise floor:** NOT a fresh replica arm — with cache ON a deepseek replica would hit the
cache and read a false-zero floor; with cache OFF it would re-run the whole pipeline
(map-drift confound + cost). Instead use the already-measured reshuffle floor: ~1pp per book
(rich_synth re-roll 26 flips, deep-block 29 flips, both documented above). This bounds what
"re-drawing without a real change" moves.

**Registered reading (frozen):** paired ΔExcess-beta (Sonnet − baseline) at 4w over trades
BOTH books take, cohort-block CI. Counts as a real improvement ONLY if the shift exceeds
the ~1pp reshuffle floor AND the cohort-block CI on the paired delta clears it. Decision-flip
COUNT is not the metric (reshuffle moves ~1pp of decisions regardless). Secondary:
marginal trades Sonnet opens that deepseek did not, and how they grade. Burned-grid rule:
this can corroborate or reject a model upgrade, it does not tune any other knob.

**Honest prior:** the fresh/OOS work above suggests the bottleneck is signal/coverage (news
volume uncorrelated with profit, edge not robust), not reasoning — so the likely outcome is
"stronger judgment does not move outcomes beyond reshuffle," which is itself a clean, useful
result (it says: spend effort on signal, not on a bigger model). Cost ~$17 (Sonnet strong
calls only, ~270 decisions × 3 calls).

**RESULT (2026-07-25) — arm COMPROMISED at cohort 6; salvaged on cohorts 1–5.**
The OpenRouter key hit its **weekly limit** mid-run: 107 of 111 `synthesis_failed` rows are
`403 Key limit exceeded`, first firing at synthesis block #155 = the 2026-05-29 cohort. Every
strong call from that point failed, so cohorts 6–9 (05-29…06-19) contain **zero real Sonnet
decisions** — proof: `traded=37` is identical whether the window is the full 9 cohorts or
just cohorts 1–5. Only 4 failures (1.5%) were genuine Sonnet `LongTermSignal` validation
errors, so Sonnet's structured-output reliability is fine; the 42.4% headline is
infrastructure, not model quality.

Salvage over the clean cohorts 1–5 (2026-04-24…05-22), paired vs `wf_30x_deep_pit_on`,
n=35 both-traded-and-graded:

| | Sonnet | baseline |
|---|---|---|
| mean excess-beta @4w | +0.10% | +1.82% |
| win rate | 54% | 66% |

- paired Δ(Sonnet − baseline) = **−1.72%**, cohort-block 95% CI **[−2.65, +0.97]** → includes 0.
- **Registered reading → stronger judgment does NOT beat baseline.** Δ is nominally negative
  and the ~1pp floor is not cleared. Consistent with the standing conclusion: the bottleneck
  is signal, not reasoning.

**Mechanism (decomposition, not speculation).** `excess_beta` depends only on direction and
prices, so same-direction pairs are arithmetically identical — measured max|Δ| = 0.0000% over
30 of the 35 pairs. **100% of the −1.72% comes from 5 direction flips, all baseline SHORT →
Sonnet LONG** (META 04-24 −34.18pp, UBER 04-24 −16.42, UBER 05-01 −13.26, JPM 04-24 −9.14,
KO 04-24 +12.66). META alone is ~57% of the delta; drop it and Δ ≈ −0.74%, inside the floor.
Broader: Sonnet ran **9 SHORTs vs baseline's 14**, and SHORT→LONG is the dominant disagreement
(6 of 11 total). Mean conviction is near-identical (0.311 vs 0.321) — the reluctance is
**directional, not global**. This window happened to reward shorts (baseline SHORT n=14,
win 71%, median +3.05%), so Sonnet was punished for declining a book that was working *here*.

**Correction to an earlier in-flight reading.** A mid-run status note called Sonnet
"systematically more conservative, ~15% traded." That was **wrong** — it was the dead key
producing NEUTRAL rows, not selectivity. The real, defensible finding is short-reluctance
measured on 5 flips, which is not significant on its own.

**Two operational traps found (worth more than the result).**
1. A `synthesis_failed` row still writes a **non-null `signal`** (the NEUTRAL fallback), and
   resume skips on `signal IS NOT NULL` (`backtest.py:155`). So quota-killed rows
   **permanently block their own retry** — any resume MUST first delete rows where
   `(signal->>'synthesis_failed')::bool`.
2. Never launch a Sonnet arm without checking `GET /v1/key` `limit_remaining` first.

### `wf_fresh30_sonnet_c15` — is Sonnet's short-reluctance protective OUT of sample?
(pre-registered 2026-07-25, BEFORE launch)

**Frozen question.** In-sample, Sonnet lost purely by declining SHORTs in a window where the
short book won. But `wf_fresh30_pit` established that on FRESH tickers the SHORT book is the
root cause of the edge collapse, and `wf_fresh30_holdout` FALSIFIED the direction split as
period-specific. So: on fresh tickers, does the same short-reluctance **help** — does the
paired delta flip sign?

**Design.** Identical treatment to `wf_30x_strong_sonnet` (STRONG_LLM_MODEL=
`anthropic/claude-sonnet-5`, LLM_PROVIDER=novita, cache ON, LT_METRICS_VERSION=2,
LT_MAX_REFLECTION=0, fill t1, horizons 14/28/42/56/84), applied to the **fresh30 universe**
(29 tickers, ABBV…WFC) over **cohorts 1–5 only** (2026-04-24…2026-05-22). Baseline frame =
`wf_fresh30_pit`, same tickers, same dates, deepseek synthesis — a clean paired comparison.

**Why cohorts 1–5, stated before looking:** this is a **budget** constraint, not a result-
dependent one. Weekly key quota at launch = $9.92; at the ledger's own ~$0.063/decision that
buys ~155 decisions, and 29 × 5 = 145 fits with margin. It also matches the exact cohort
range salvaged in-sample, so the two Sonnet arms are directly comparable. Cohorts 6–9 are
deliberately out of scope and are NOT to be back-filled into this entry later.

**Quota discipline (fixes the trap above).** The runner checks `limit_remaining` before each
cohort and stops cleanly if it is below a 1.4× cohort-cost margin, so no cohort is ever
half-written with `403` rows. If a cohort does get poisoned, its `synthesis_failed` rows must
be deleted before any resume.

**Registered reading (frozen).** Paired ΔExcess-beta (Sonnet − `wf_fresh30_pit`) at 4w over
trades both books take, cohort-block CI, judged against the same ~1pp reshuffle floor.
- **Confirms "short-reluctance is protective"** only if Δ is **positive** AND clears the ~1pp
  floor with a cohort-block CI excluding 0.
- Δ ≈ 0 or negative → the reluctance is not a virtue either; Sonnet is simply a different
  draw, and the model-upgrade lever is dead in both directions.
- Mandatory sanity gate, declared now: report `synthesis_failed` rate FIRST and exclude those
  rows. A rate >5% invalidates the arm — do not read returns off a quota-degraded book again.
- Secondary (diagnostic, NOT the verdict): SHORT count Sonnet vs baseline, and the
  direction-flip decomposition, to confirm the mechanism is the same one measured in-sample.

**Honest prior.** Genuinely uncertain, which is why it is worth running — but the base rate
in this ledger is unkind: n≈145 decisions yields maybe ~30 paired trades, the flip count will
again be single-digit, and every OOS cell so far has come back underpowered. Most likely
outcome is a wide CI containing zero. A sign flip would be interesting but must clear the
floor to count.

**RESULT (2026-07-25) — hypothesis NOT confirmed; and the MECHANISM ITSELF FAILED TO
REPLICATE, which is the bigger finding.**

Run was clean, unlike the in-sample arm: 145/145 decisions, **4 `synthesis_failed` (2.8%)**,
**zero 403s**, all 5 cohort transitions `rc=0`, cost $5.70. Health gate **PASS** (<5%).
The per-cohort quota gate never had to fire; measured cost was $0.040/decision, not the
$0.063 assumed at registration.

| | Sonnet | baseline (`wf_fresh30_pit`) |
|---|---|---|
| traded | 21 (LONG 12 / SHORT 9) | 28 (LONG 18 / SHORT 10) |
| mean excess-beta @4w (paired) | +1.06% | +1.50% |

- paired Δ = **−0.44%**, cohort-block 95% CI **[−1.11, +0.62]** → includes 0, does not clear
  the ~1pp floor.
- **Registered reading → NOT confirmed.** Short-reluctance is not demonstrably protective
  out of sample. Per the frozen alternative branch: the model-upgrade lever is now dead in
  **both** directions — Sonnet neither beats nor is beaten by baseline once you leave the
  one window where its flips happened to matter.

**The mechanism did not replicate — this falsifies "short-reluctance" as a model property.**
The in-sample story was: Sonnet systematically declines SHORTs (9 vs 14, and 5 of 5 flips
were SHORT→LONG). Out of sample that pattern is simply absent:

| | Sonnet SHORT | baseline SHORT | ratio | flips SHORT→LONG |
|---|---|---|---|---|
| in-sample (`wf_30x`) | 7 | 14 | 0.50 | 5 of 5 |
| OOS (`fresh30`) | 9 | 10 | **0.90** | **0 of 2** — both flips ran LONG→SHORT, the opposite way |

So the in-sample −1.72% was **not** a behavioral trait that generalizes; it was 5 idiosyncratic
flips in one window. An earlier in-flight hypothesis in this ledger — that the reluctance might
be a *virtue* on fresh tickers where the SHORT book collapsed — is **rejected**: there is no
reluctance to be virtuous.

**Same fragility as in-sample, one notch worse.** 16 of 18 paired trades are same-direction
and therefore arithmetically identical (measured max|Δ| = 0.0000%). All of the −0.44% comes
from 2 CVX flips, and effectively from **one**: CVX 2026-04-24 contributes −0.49pp; drop that
single trade and Δ = **+0.06%**. A result that inverts on one trade is not a result.

**Power, stated honestly.** The registration guessed ~30 paired trades; actual was **18**,
because fresh tickers produce heavy `NO_DATA` (<8 articles/60d) and Sonnet traded only 21 of
145 decisions. Flip count was 2. This cell could not have detected a 1pp effect even if one
existed — treat "CI includes 0" here as *uninformative about small effects*, not as evidence
of no effect.

**Standing conclusion, now supported from two independent cells:** the strong-judgment lever
does not move outcomes. Both Sonnet arms reduce to a handful of direction flips whose sign is
window-specific. Spend effort on signal/coverage, not on a bigger judgment model.

### `midday_mon` — in-session Monday run (registered 2026-07-22 as a FUTURE experiment;
NOT scheduled, prerequisites below)

**Origin and honest status of its premise.** Proposed by the operator to close the
reaction blind spot: the agent sees no post-Friday price (no futures, no premarket), so it
enters Monday's open without knowing how much of its news the open already absorbed. The
protective half of that premise — "skip what the open already ate" — was tested the same
day as `gap_guard` and REJECTED WITH INVERSION: the entries a 2% gap filter would have
blocked were the best trades in every book. What survives is the *observability* half:
deciding at midday, the agent can SEE the morning reaction and condition on it (in either
direction — the burned books hint continuation, not exhaustion, but that hint is
data-born and unregistered). Later entry never gets a better price; it buys an informed
decision about the price.
**Frozen design (one formulation):** decision Monday 12:00 ET; visible: weekend+morning
news, Friday dailies, and intraday bars 09:30→12:00 (data exists: `historical_data_*`,
1-min, RTH-only, since 2026-02-23); entry = open of the 12:01 bar (`fill=intraday`,
horizons anchored there); same 30 tickers, deep on, provider pinned, cohorts run ON their
Monday (drift lesson, 2026-07-22). Compared against the concurrent standard book on the
same weeks, paired by week — the pairing methodology proven on monday-vs-friday.
**Prerequisites, in order — it does NOT launch until all three hold:**
1. **The free ~2026-08-10 maturation read is taken first** (amended 2026-07-22: the
   operator closed the Monday line and chose midday as the successor, so this read no
   longer GATES the launch — but it costs $0, arrives anyway, and is the only
   day-time-family evidence there will ever be; designing midday without reading it
   would be discarding paid-for information).
2. **Engineering:** a minute-granularity PIT cutoff in `tools/pit.py` and an `intraday`
   fill mode in `exit_rules.py` — both tested to the standard the daily cutoffs got.
3. **Multiplicity acknowledged at launch:** this is regime variant #3 (Sunday, Mon 06:30,
   Mon midday). Whatever wins the family must clear its bar with that count on the table.
**Evidence:** forward cohorts only. Burned windows may reject, never accept — and the one
midday-relevant diagnostic they already gave (gap continuation) cuts AGAINST the
protective story this experiment was first imagined for.

- **`wf_30x_deep_pit_on` / `_off` — does deep analysis earn its keep? (pre-registered
  2026-07-20, launched same day).** Deep v1 is ON in production, costs a large share of
  every run, and rests on #9 (+1.78%, since matured down to +1.56%), which ran with F1–F4
  open — the holes lived in the deep path itself, and the ±0.2 advisory nudge moves the
  traded set. **Design change vs the audit's prescription:** not a comparison against the
  stored #9, but **two fresh legs run back-to-back on the same day** — because a
  deep-enabled replay is not reproducible across days (measured: 10 cache misses and
  conviction 0.82 → 0.78 on an identical cohort), so an old-vs-new comparison would
  confound the PIT fixes with graph drift. Both legs: standard 30×9 grid
  (2026-04-24…06-19, the most-matured window), `LT_METRICS_VERSION=2 LT_LLM_CACHE=true
  LT_MAX_REFLECTION=0`, horizons 14/28/84, SPY, fill `open_t1`, graded at
  `--asof-today 2026-07-20`; the ONLY difference is `LT_DEEP_ANALYSIS`.
  **Demote-only** (burned grid, rule 1): the outcome can retire deep, never re-accept it.
  Frozen reading: deep stays live only if ON beats OFF by >1pp mean 4w excess **and** the
  gap survives the cohort-block CI; a gap inside ±1pp is "not worse ⇒ but not paying for
  itself" and, given the cost, argues for `LT_DEEP_ANALYSIS=false`; ON losing by >1pp
  retires it outright. Both legs also stamp `gate_trace`, which is what `sel_null`'s
  stage 1 then consumes offline for free.
  **Verdict (2026-07-20): deep does NOT pay for itself ⇒ demote to `LT_DEEP_ANALYSIS=false`.**
  540/540 runs, 0 errors, 55 directional rows per leg, `gate_trace` on all 270 rows each.
  4w traded mean excess: ON **+2.77%** (n=44) vs OFF **+1.81%** (n=47) = **+0.96pp**, inside
  the ±1pp band ⇒ the frozen "not worse, but not paying for itself" branch. Neither leg's
  cohort-block CI excludes zero (ON [−0.40,+6.49], OFF [−2.39,+5.96]). 2w agrees and is
  weaker still: +2.59% vs +2.13% = +0.46pp.
  - *A cohort-weighted gap would read +1.11pp (block CI [+0.05,+3.01]) and clear the bar.
    It is NOT the verdict:* the pre-registered statistic is the book's mean 4w excess
    (trade-weighted), and this alternative was computed after seeing the data. Recorded
    so the choice is visible, not so it can be swapped in.
  - *Mechanism — the finding that outlasts the verdict.* **Deep changed direction on 0 of
    55 signals.** Every shared trade has a bit-identical excess; the two books differ ONLY
    in that ON's conviction nudge pushed 3 signals below 0.55 (AVGO 04-24 LONG −6.06%,
    GOOGL 05-01 LONG −7.74%, ADBE 06-19 SHORT −23.27%). So deep's entire causal channel is
    *conviction → threshold*, and the whole +0.96pp is 3 lucky exclusions out of 55 — one
    of them (ADBE) carrying two thirds of it. That is far too thin to fund a large share
    of every run's cost.
  - *Interaction with `sel_null`, decisive.* In the `vetoes_only` arm both legs produce the
    **identical** book (n=55, +2.93%) — with no conviction threshold, deep is a measured
    no-op. If `sel_null` retires the threshold, deep is worthless by construction; if the
    threshold stays, deep buys 3 coin-flips. Both branches point the same way.
  - *Version asymmetry, disclosed:* leg ON ran before `74e2965` landed (`selection_mode`
    absent from its provenance), leg OFF after. The refactor is behaviour-preserving by
    test, and independently: `ab_selection`'s new `selection_passed()` reproduces the ON
    leg's stored `traded` set exactly (44/44), decisions that were made by the old inline
    code. Noted anyway — it is the failure mode this ledger keeps catching.
  **Action: recommend `LT_DEEP_ANALYSIS=false` in the Track A redeploy bundle** (demote-only,
  reversible, cuts a large share of per-run cost). Awaiting the operator's go.

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
  **Stage 1 run 2026-07-20 ($0, on the two `wf_30x_deep_pit` legs, the first books carrying
  `gate_trace`): the null SURVIVES on all three criteria, in both books.**
  Sanity check passed first — the incumbent arm reproduces each stored `traded` set exactly
  (44/44 and 47/47), so the replay is faithful and the arms differ only by the rule.
  | book | incumbent | vetoes_only | N1 Δ | N2 count | N3 worst |
  |---|---|---|---|---|---|
  | `_on`  | +2.77% (n=44) | **+2.93%** (n=55) | +0.16pp PASS | 1.25× PASS | +0.00 PASS |
  | `_off` | +1.81% (n=47) | **+2.93%** (n=55) | +1.13pp PASS | 1.17× PASS | +0.00 PASS |
  Ungated beats gated in both, the worst trade is **unchanged** (−23.87% is a trade the gate
  kept, so the penalties are not buying the tail protection they were suspected of buying),
  and turnover rises well under the 2× cap. This is now **five books** pointing the same way.
  Still NOT an acceptance: burned grid, reject-only (rule 1) — it means the null was given a
  fair chance to die and did not. Next is stage 2 on the unburned Mondays, blocked on D2
  (transaction costs), since the challenger's edge must survive ~20–25% more turnover.
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

### `intraday_curve34` — profit vs holding time, intraday Monday entry (pre-registered 2026-07-29, BEFORE launch)

**Not `midday_mon`.** That entry (registered 2026-07-22) asks whether a midday decision beats
the morning one, is frozen to 30 tickers / 12:01 fill / provider pinned, and is **forward
cohorts only**. This is a different question — the SHAPE of profit against holding time —
and it runs on a replayed window, so it inherits none of that entry's acceptance rights and
does not consume its prerequisites. `midday_mon` stays open and unspent.

**Question.** A position the long-term agent opens Monday **inside the session** — decision
stamped 12:00 ET, agent given 1 minute, fill at the open of the first bar starting strictly
after (12:02) — has what profit as a function of how long it is held? Every horizon is
evaluated on the SAME entry; only the exit moves.

**Fill convention `mid_1m`** (new; `longterm_agent/intraday_entry.py`). The first intraday
fill in this repo — every prior book used daily bars (`open_t1`, `open_d0`). Exits form two
legs glued to one axis of *session minutes held* (overnight counts zero, being untradable):
intraday 1…240 min on the entry day's minute bars, then closes of trading days 0…60. The
legs meet at `eod:0`; that seam is printed as a data check (measured: 16:00 minute print vs
official close, median 0.027%).

**Grid.** 14 Mondays 2026-04-27 → 2026-07-27, Memorial Day 05-25 rolled forward to Tuesday
05-26 (`--roll-holidays`; rolling beats skipping on a block-starved book). 04-13 and 04-20
excluded: the March holdout family ran every live name on them.

**Universe — 34 low-burn names, chosen by burn intensity, not by a fresh/burnt flag.**
Burn is three-level over these cohorts: 21 names untouched (0 sweeps), 13 lightly touched
(1 sweep `wf_extra13`, 7/14 cells, ≤2 trades), 30 heavily burned (23 sweeps, up to 8 trades,
all dissected in `FAILURE_MODES_monday_t1_pit.md`). The book takes the first two levels:
**476 cells, 81% untouched, 19% lightly touched, zero heavily burned**, constant composition
across all 14 cohorts. All 34 are drawn from the live production universe (snapshot
2026-07-26). Sector deviation from production **15%** (the untouched 21 alone would be 26%;
the 13 supply exactly the tech/financial weight prior sweeps burned away).

**Settings, frozen:** `LT_LLM_CACHE=true LT_MAX_REFLECTION=0
LT_BUCKET_CACHE_VERSION=0.1.0-mid1200`, deep analysis ON (as live today), detection v1,
models `deepseek/deepseek-v4-pro` + map `google/gemini-3.1-flash-lite-preview`, temperature
0, `--horizons 28 --workers 8 --no-grade`. The bucket-cache namespace is deliberate: keyed
`(ticker, week_start)`, it would otherwise hand this sweep the 06:30 grid's weekly summaries,
whose bucket boundaries differ by 5.5h. **`--no-grade` is mandatory** — `backtest.py`'s daily
grader would fill at 09:30, hours before a midday decision exists.

**Provider NOT pinned — disclosed.** `llm_provider_routing()` returns None, so requests may
land on any of 18 backends serving the model. `midday_mon` requires pinning; this entry does
not, and the resulting quantisation drift is a known limit rather than a controlled variable.

**Benchmark.** Equal-weight over the book for the entry-day stub (no ETF minute table exists
for this window — `historical_data_spy` was never fetched; the intraday fetcher's missing
`BENCHMARK_TICKERS` split was fixed 2026-07-29 but Yahoo's 30-day 1m cap means April–June
cannot be backfilled), then **real SPY daily closes** from the entry close onward. So the
synthetic part is one afternoon; the multi-week leg that carries the variance is graded
against the same instrument every daily book here uses. `--benchmark ew` is the robustness
arm (measured 2.6pp mean difference at `eod:20` on a plumbing check).

**Pre-registered readings.** Primary horizon `eod:20` (≈4w, the agent's own). Primary
statistic: mean signed excess with a **cohort-block** bootstrap CI. Support needs all three —
CI excludes zero, sign-flip `p<0.05` (one coin per cohort), and the mean beats the
direction-blind always-long control. The **shape** claim is decided on the paired table
(each horizon minus `eod:20` on the same trade), which cancels the cohort shock before
averaging and is the only reading with real power at 6–9 blocks; it must also survive
`drop`/`clamp`/`balanced` and `--agent-minutes 2` (measured pipeline p50 is 121s, so the
1-minute premise is ~2× optimistic). Secondary readings, all fixed now: the untouched-21
sub-book as the strict-burn check; without `NOW`/`COST` (their tickers collide with English
words, ~6% of the book); `--all-directional`; `--cost-bps 10`.

**Multiplicity acknowledged:** day-time family variant #3 (Sunday, Mon 06:30, Mon midday).
The reported maximum over the ~26-horizon grid is a SELECTED maximum, denominator 26.

**Power, stated before the run.** ≈35–45 trades in ~6–9 non-empty cohorts (`monday_t1_pit`
yielded 41 trades over 12 Mondays with 29 on two of them, and five consecutive Mondays where
the detector fired on 0–1 of 30 names with max conviction 0.20–0.25 against a 0.55 gate — so
this is a detector property, not a sampling accident, and no universe widening fixes it).
An inconclusive result is the likely outcome and must be reported as inconclusive.

**Cost, measured not estimated:** $0.039/run at the coldest point of a sweep (first cohort,
empty bucket cache), converging lower as consecutive Mondays share 8 of 9 weekly buckets →
**476 runs ≈ $14**, ~4-6h at 8 workers. Full write-up:
`docs/PREREG_MONDAY_MIDDAY_HOLDING_2026-07-29.md`.

**Status: registered, NOT launched.** A 13-row fragment under the discarded id
`monday_mid1200` (cohort 2026-04-13, outside this grid) and a 1-row `monday_mid1200_smoke`
exist from a design that was superseded; neither is read by this experiment.

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

- **Beta-adjusted book — ADDED 2026-07-20** (`longterm_agent/beta.py`; `beta` +
  `excess_beta` on `backtest_runs` and `longterm_agent_results`, printed beside the hold
  and stopped books). `excess` scores every trade against SPY at an implicit beta of 1,
  which punishes a defensive long for being defensive: in #15, PEP and KO priced at
  beta ≈ −0.03 and carried a mechanical ~−5pp each before any judgement. `excess_beta =
  sign · (stock − beta · bench)` — i.e. `trade_excess` with beta in place of 1, so the
  books stay directly comparable; beta is estimated from bars strictly before the entry.
  Reporting only, backfilled free over all books. First readings (4w traded):
  `monday_t1_pit` +0.18% → **+0.50%** beta-adj (median beta 0.96);
  `wf_30x_f12` +0.27% → **+0.03%**; `wf_30x_v0.2_deep` +1.56% → **+1.25%**.
  Aggregate effect is small (beta explained ~4% of dispersion), as expected — its value
  is per-trade attribution, which is why it belongs in the report and not in a gate.
- **METHODOLOGY CHANGE 2026-07-20 — `excess_beta` becomes the primary metric, forward only**
  (`LT_PRIMARY_METRIC`, default `excess_beta`; recorded in provenance knobs). Rationale:
  scoring at an implicit beta of 1 mis-attributes factor exposure to skill in BOTH
  directions — it flattered the beta-1.15 longs of `wf_30x_deep_pit_on` (+3.19% → +2.69%)
  and booked a SHORT of a beta-1.26 name that LAGGED its own beta as a loss
  (`monday_t1_pit` shorts −0.63% → +0.63%). Beta is estimated from bars **strictly before
  entry** (`date < entry_date`), so it is known at decision time and adds no lookahead —
  it is legitimate to pre-register a criterion against it.
  **Scope, and why the boundary matters.** Applies ONLY to experiments whose results do not
  yet exist. Verdicts already frozen under `excess` are NOT re-read, because here the ruler
  decides the answer: **the deep ON/OFF gap is +0.97pp under `excess` and +1.09pp under
  `excess_beta`, either side of the pre-registered 1pp bar.** Re-reading B1 would reverse a
  frozen verdict by changing the metric after seeing the result, which is exactly the
  failure this ledger exists to prevent. B1 therefore stands as recorded: demote.
  *Checked before adopting:* `sel_null` stage 1 is unaffected — N1 is +0.16pp (`excess`) vs
  −0.23pp (`excess_beta`) on `_on` and +1.13pp vs +0.85pp on `_off`, all far from the −1pp
  rejection bar, so the null survives under either ruler.
  **Consequence for B1, recorded honestly:** a verdict that sits at 0.97 vs 1.09 across two
  defensible metrics is *fragile*, not robust — and both cohort-block CIs still cover zero.
  The mechanism finding is what carries weight (deep changed direction on 0 of 55 signals;
  the whole gap is 3 trades, two thirds of it one ADBE). The clean resolution is the free
  ~2026-08-10 maturation re-read under `excess_beta`, pre-registered **now**, before those
  results exist. `ab_selection` prints the secondary metric beside the primary on every run,
  so a ruler-dependent verdict can never again be invisible.
- **Maturation update 2026-07-20 — the two headline books DETERIORATED as cohorts
  matured.** Re-grading at `--asof-today 2026-07-20` (previous grades used 07-10) added
  ~60 newly matured rows to each: **#8 `wf_30x_f12` 4w +0.85% → +0.27%** (n 46 → 49) and
  **#9 `wf_30x_v0.2_deep` +2.34% → +1.56%** (n 39 → 42). No re-run, no code change — only
  more of the same window's outcomes arriving. Both moves are in the demote direction and
  compound the fill-convention and PIT corrections above; #9's pending leak-free re-check
  should be read against +1.56%, not the +1.78% originally recorded.

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


- **Daily bars moved to a DAILY job — 2026-07-28** (`bloomberg_news_fetcher/config.py`:
  `DAILY_BARS_FETCH_*`, default 20:30 ET, `period=1mo`). They used to ride along with the
  weekly Saturday fundamentals fetch, which put prices on a filing calendar instead of a
  market one. Two measured costs, both surfaced while repairing the 2026-07-24 incident:
  a cohort opened on Sunday had **no SPY entry bar until the following Saturday**, so
  `/open` computed vs-SPY for only part of the book (6 of 12 positions), and the book read
  **−0.9% on that subset vs +0.7% over all twelve** — the aggregate was not wrong so much
  as quietly describing a different set; and a bad fetch sat uncorrected for days because
  nothing re-read the window. The refetch window is deliberately wider than the cadence:
  that overlap is what heals a bar the writer drops as non-finite, with no manual repair.
  Prices, grading inputs and the freshness preflight are no longer gated behind
  `FETCH_FUNDAMENTALS`. First run: 2719 rows, 0 failed, **2 bars dropped as non-finite** —
  Yahoo still serves them, and they are now refused instead of stored.
- **`/open` reports the book aggregate — 2026-07-28** (`stats.book_totals`): equal-weight
  return, vs-SPY, up/down count and a long/short split, on the `/open` text and the chart
  subtitle. Equal-weight because there is no position sizing in this system, which also
  means the figure cannot be turned into a currency amount without inventing capital. When
  the vs-SPY leg covers fewer positions than the book leg, the coverage is stated inline —
  the two numbers sit side by side and otherwise read as one sample.

### `live_0726` — NaN price poisoning cost a whole live cohort (incident 2026-07-24/26, fixed 2026-07-28)

**Symptom, as the operator saw it:** the bot showed no new open positions for a week.
`/open` kept serving the six 2026-07-19 positions. Nothing in the book had changed.

**What actually happened, in three steps.**

1. **Sat 2026-07-25 03:00 ET** — the weekly `fetch_all_daily(period="1mo")` top-up wrote the
   2026-07-24 bar for **all 131 tickers with `close = NaN`** (`adj_close` too; OHLV were
   valid). yfinance returns exactly this shape when a session has not finalised on Yahoo's
   side. `save_daily` did `float(row["Close"])` — which does not raise — and Postgres
   `numeric` stores NaN happily. No error anywhere.
2. **Sun 2026-07-26 06:00 ET** — the weekly batch ran on that data.
   `{'runs': 64, 'signals': 1, 'trades': 1, 'no_data': 5, 'errors': 49}`.
   The 49 died at `valuation_pit`: `round(mktcap)` → `ValueError: cannot convert float NaN
   to integer`. The guard above it tests `price is None`, and **NaN is not None**.
3. **The survivors were lost too, silently.** For them `valuation_pit` returned early with
   `price: NaN` still in its dict; that NaN reached the JSONB payloads and Postgres refused
   every insert (`invalid input syntax for type json`). `persist_run` caught it and
   *printed* — so the batch reported `trades: 1` while `longterm_agent_results` received
   only the 5 NO_DATA rows. **The one trade of that week, WMT LONG conv 0.55, exists in the
   op log and nowhere else.**

**The instructive part is step 3, not step 1.** A bad upstream feed is ordinary; what turned
it into a lost week is that the failure was *reported as success*. Downstream — `/open`,
`/week`, grading — an unpersisted trade is indistinguishable from "the agent had no
opinion". 49 crash alerts did fire (`LT_ALERT_ON_ERROR` defaults on); the missing book did
not.

**Fixes applied 2026-07-28 — four layers, any one sufficient:**
- `save_daily` drops a bar whose OHLC is non-finite, and says so. A missing date is loud
  (the freshness preflight sees the gap); a NaN close is not.
- `CHECK (… <> 'NaN'::numeric)` on `stock_prices_daily` — the guarantee that does not
  depend on remembering, for any writer ever. NULL still passes; NaN cannot be stored.
- `_close_on_or_before` excludes NaN **in SQL**, so the lookup falls THROUGH a poisoned bar
  to the last good close rather than returning nothing; `valuation_pit` sanitises `price`
  at its boundary so no NaN can reach a JSONB write.
- `persist_run` failure → `oplog.error("PERSIST_FAILED")`, counted as `not_persisted` in the
  batch stats, and the Telegram summary contradicts its own headline when it is non-zero.
- Regression tests: `longterm_agent/tests/test_nan_price_guard.py` (the DB constraint is
  what makes the end-to-end repro impossible — the row can no longer be inserted).

**Data repair (2026-07-28):** 132 poisoned rows. 79 refilled with real bars now available;
**53 set to NULL close** — Yahoo *still* has no usable 2026-07-24 bar for those names four
days later (absent, or padded all-NaN with volume 0), so the honest state is "no bar", which
is what the fixed writer would have produced. SPY and all six open positions repriced clean.

**Backfill — registered BEFORE the run, per the no-re-roll protocol.** The 5 persisted
NO_DATA rows stand untouched; the 59 that never landed are patched by the batch's own
resume-skip (`_already_done`), which is exactly the "patch rows INDIVIDUALLY, never
re-roll" remedy. Run 2026-07-28 at `as_of=2026-07-26`, `--no-alert`, live regime
(`LT_METRICS_VERSION=2`, no provider pin, cache off — matching the original).

**Honest limit, stated up front and BEFORE seeing the output.** The decision inputs are
PIT-clean (every reader is cutoff-gated at `as_of`, so no post-07-26 news, filing or bar is
visible), but the run happens two days late and the fill convention enters at the
**2026-07-27 open, which had already printed when the run started**. These rows are
therefore *not* what a live Sunday run would have produced — the measured replay-drift
mechanism applies (transient call failures leave cache holes that cascade), and the entry is
retro-known even though the decision is not. Read `live_0726` as a recovered detection
record, **not** as clean forward P&L evidence; the six 2026-07-19 positions remain the
uncontaminated live book.

**RESULT (2026-07-28) — cohort recovered, and the caveat above turned out to be the finding.**
Pass 1: `{'runs': 59, 'signals': 7, 'trades': 6, 'no_data': 0, 'errors': 0, 'skipped': 5,
'not_persisted': 1}` — **0 errors against 49 in the live run**, which is the fix confirmed on
the full universe rather than on a smoke test. Pass 2 patched the one straggler
(`--tickers` equivalent via resume-skip). Final cohort: **64 rows, 6 trades** — AVGO, JNJ,
NOW, TMUS, WFC LONG; WMT SHORT.

**Replay drift, measured against the live run's own op log.** Ten tickers survived
2026-07-26 and were re-decided here on identical PIT inputs:

| live 2026-07-26 | backfill 2026-07-28 |
|---|---|
| BRK-B, CAT, KO, MA, SBUX, V — NEUTRAL 0.05 | identical, to 2dp |
| COST, CRM, PEP — NEUTRAL 0.15 | identical, to 2dp |
| **WMT — LONG 0.55 (traded)** | **SHORT 0.78 (traded)** |

**9 of 10 reproduce exactly; the only ticker carrying a directional call flipped sign.**
A second instance fell out of the incident itself: NOW came back SHORT in pass 1 (visible
only in the `PERSIST_FAILED` payload, since the write failed) and **LONG 0.58 in pass 2**.
Two directional calls re-run, two flips, while every low-conviction NEUTRAL is bit-stable.
Consistent with the PEP case that produced the no-re-roll rule, and it sharpens it: the
drift is not diffuse, it concentrates exactly where synthesis makes a contentful call.

**Consequence — the lost decision is NOT recoverable.** The live book said WMT LONG; the
reconstruction says WMT SHORT. Nothing determines which one "the agent would have traded",
so `live_0726` stands as a recovered DETECTION record and must not be read as forward P&L.
The six 2026-07-19 positions remain the only uncontaminated live book. Note also that JNJ
is now open in both cohorts (0.65 @ 07-19, 0.72 @ 07-26) — weekly cohorts are independent
by design, but the book carries double weight on that name.

**Second-order damage found and repaired.** The poisoned prices had already been graded:
**21 `shift_library` rows were filled on 2026-07-25 against NaN closes**, because
`round(nan, 4)` returns nan without raising. Those NaN outcomes passed `IS NOT NULL` in the
analog query and reached `median()` — which is what destroyed the NOW write, three days
after the prices themselves were fixed. This is the incident's most useful lesson: a data
defect does not stay in the table it landed in. Cleared and re-graded against the repaired
series (21 rows, range −17.3%…+15.3%); `longterm_agent_results` and `backtest_runs` were
verified clean. Guards added at every layer that touched it: the grader now leaves a row
UNGRADED rather than writing a non-finite outcome, the analog pool drops non-finite rows,
`persist_run`/`write_snapshot` scrub non-finite floats to NULL and log the field paths, and
CHECK constraints (`x > '-Infinity' AND x < 'Infinity'`) cover 6 columns on `shift_library`
and 14 on `longterm_agent_results`.
