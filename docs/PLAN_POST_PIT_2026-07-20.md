# Development plan after the PIT / fill-convention fixes (2026-07-20)

Companions: `PIT_RULES.md` (how to write code that cannot leak), `PIT_AUDIT_2026-07-19.md`
(the evidence), `WALK_FORWARD_PROTOCOL.md` (how to read a backtest), `BACKTEST_LEDGER.md`
(what has been run), `MODIFICATION_PLAN.md` (the phase backlog this re-prioritises).

---

## 1. Where we actually are

Two corrections landed in 48 hours and together they reset the evidence base:

- **Fill convention** (`open_t1`): trades were being filled at a price printed *before* news
  the decision had read, and the untradable decision→entry gap was credited to the book.
- **PIT audit** (F1–F10): the deep-analysis path read future graph events, final story
  sizes, and same-day documents; under `open_d0` that was a *post-entry* leak.

The measured consequence, on the same grid with the same frozen criteria, changing only the
fixes: **4w mean excess +0.89% → +0.18%, 2w +0.61% → −0.82%** (`monday_t1` → `monday_t1_pit`).
Roughly +0.7pp (4w) and +1.4pp (2w) of the apparent edge was lookahead.

**So the honest state is: no trustworthy positive evidence of edge, and none of the retro
grids can supply it.** Specifically:

| Claim | Status now |
|---|---|
| #9 deep v1 "nudge HELPS" (+1.78%) | contaminated-in-principle — and deep is ON in live on this basis |
| #8 Phase 12 (+0.53% → +0.85% after regrade) | fill-corrected, but deep-OFF so PIT-fix-neutral; still one-regime, burned grid |
| `monday_t1_pit` deploy regime | INCONCLUSIVE (+0.18%, block CI [−9.61%, +1.64%] over 3 cohorts) |
| Conviction as a sizing input | still anti-correlated on the tails (top bucket worst at 4w) |
| Live book | 7 traded rows total — effectively empty |

Everything else in the ledger sits on the same ~12-week window, which has now hosted **15
backtest ids**. Under the burned-grid rule that window can only reject, never accept.

## 2. The binding constraint: cohorts, not trades

F10 changed what "sample size" means here. Trades inside a cohort share a market week, so the
currency is **independent cohorts**. `monday_t1_pit` had 30 graded 4w trades — but in **3
cohorts**, and the block CI came out ~11pp wide.

Scaling that width as `1/√n`, resolving a ~1pp effect needs on the order of **~90 independent
cohorts**. At one cohort per week that is ~2 years of pure forward accumulation. That number,
not any single verdict, is what this plan has to attack.

Three levers, in order of cost-effectiveness:

1. **Reduce per-cohort variance** — more tickers per cohort averages away idiosyncratic noise
   (we measure excess vs SPY, so the common factor is already netted). Cheap.
2. **Use unexamined cohorts that already exist** — every grid so far used Apr–Jun 2026 only.
   News go back to 2024-12-11. Gated by model knowledge cutoff (§5).
3. **Increase the effect** — better signal. This is Track E, and it is the actual goal.

---

## Track A — Integrity: ship the fixes (URGENT, blocks everything)

The server still runs **pipeline 0.8.0** with the retired grading convention.

**To be precise about why this matters — it is NOT leakage.** A live run cannot read the
future: at 06:00 on Sunday the future does not exist in the database. The F1–F5 fixes are
about *replay* honesty and are no-ops live (nothing future-dated is there to filter; a
filing "dated today" at 06:30 has not been filed yet). Three real reasons remain:

1. **Live results are being mis-measured.** The deployed grader still fills outcomes with
   the retired close-to-close rule, so it credits the untradable decision→entry gap to the
   trade and leaves `fill_convention` NULL. **7 live trades are queued for grading right
   now** — they would land in the book under a different convention than the 2 rows already
   graded as `open_t1`, i.e. one table, two definitions of "return".
2. **Backtest and live must be the same agent.** With live on 0.8.0 and every sweep on
   0.9.0, no backtest result transfers in either direction — which makes Tracks B–E
   unusable for deciding anything about production.
3. **One genuine live correctness bug rides along (F4).** `Story.article_count` was broken
   as a counter (merges add 1 instead of absorbing: a story storing 2 had 921 sourcing
   articles), and it drives 40% of the novelty score. That number was wrong in live too —
   a broken counter, not lookahead.

| # | Item | Size |
|---|---|---|
| A1 | Rebuild + push `longterm-agent` (b379757, pipeline 0.9.0); recreate the container | S |
| A2 | Re-run `regrade_fill_convention.py --apply` after the swap (idempotent) — catches anything the old grader touched in between | S |
| A3 | Rebuild `stock-fetcher`: dead Bloomberg `opinion` feed (404 every cycle), `SQ`→`XYZ` | S |
| A4 | Verify in-container: `pipeline_version 0.9.0`, scheduler up, first grade run clean | S |
| A5 | Add `TELEGRAM_CHAT_ID` (alerts silently disabled since deploy) | S |

**Until A1 lands, every new live grade is written with the retired convention** — the
decisions are fine, the scoring of them is not.

## Track B — Re-validate what live runs on contaminated evidence

| # | Item | Cost | Rule |
|---|---|---|---|
| B1 | `wf_30x_deep_pit` — re-run #9 leak-free | ~$3 | demote-only (burned grid) |
| B2 | If B1 demotes: turn `LT_DEEP_ANALYSIS=false` live | S | deep is ~40% of run cost and its only support is #9 |
| B3 | Re-calibrate `STORY_SATURATION_ARTICLES` — F4 changed the article-count unit, so the novelty constant is mis-scaled | S | open follow-up from the audit |

B1 is the single highest-value backtest left: it decides whether a feature that is ON in
production, costs real money per run, and was accepted on leaked evidence, stays.

## Track C — Buy statistical power cheaply

| # | Item | Effect | Size |
|---|---|---|---|
| C1 | Live universe **64 → 120** (`LT_UNIVERSE`, or extend `COMPANY_NAMES`; prices already cover 120) | ~6→11 trades/week, lower per-cohort variance | S (+~$2/wk) |
| C2 | **D1 knowledge-cutoff gate** — stamp each model's cutoff in `provenance()`; preflight refuses cohorts before cutoff+margin | prerequisite for C3; also gates `wf_30x_strong` | M |
| C3 | Extend the evaluation window **backwards into unexamined post-cutoff cohorts** (~Jan–Apr 2026, ~17 weeks never looked at) | ~2× independent cohorts for ~$5 | M |
| C4 | Decide + record whether unexamined history counts as a holdout | governs how C3 may be read | — |

**C4 is an owner decision, not an engineering one.** Unexamined past cohorts are a legitimate
holdout in the sense that no feature was designed against them; they are weaker than forward
data because the models may retain 2026 news in training. C2 must land first so the boundary
is enforced rather than assumed. If the answer is "reject-only, like the burned grid", C3 is
still worth running — it just cannot accept.

## Track D — Make the forward book the instrument

The protocol already says the forward book is the final arbiter. It currently has 7 rows.

| # | Item | Why |
|---|---|---|
| D1 | **Pre-register the forward acceptance test now**, before the data exists — horizon, n, criteria, and the stop rule | otherwise the forward book becomes another mined dataset |
| D2 | **Transaction costs + borrow as a third book** (was parked) | at +0.18% mean excess, costs plausibly consume the entire effect — this is now decision-relevant, not cosmetic. **Promoted to a blocking dependency** of `sel_null` (docs/AB_PLAN_selection_stage.md), whose challenger raises turnover ~40% |
| D3 | Forward report: stopped book beside hold, cohort-block CI, `fill_convention` shown | reporting parity with the backtest path |
| D4 | Weekly telemetry: hedged-share / drama-share per batch | mechanism metric for the calibration work |

## Track E — Capability (the actual goal)

Only meaningful once A–B land, because it learns from graded outcomes.

| # | Item | Depends on |
|---|---|---|
| E1 | **Phase 11 learning loop** — per-node targets are already being collected; build the export → tune → eval cycle | trustworthy grades (A1, A2) |
| E2 | `wf_30x_strong` — run on **unburned** cohorts (C3/forward), not the burned grid | C2 (cutoff gate), C3 |
| E3 | Conviction gate `llm` → `table` A/B | needs graded forward outcomes; calibration still non-monotonic |
| E4 | Deterministic catalyst-guard (code-level, the INTC/QCOM class) | pre-registration; #13 showed the prompt version cannot work |

Phase 9 data breadth, Phase 14 regime features, and the futures/weekend-context idea stay in
the backlog **below** E1 — more inputs cannot help while the measurement of whether they help
is this weak.

## 5. Sequencing

```
NOW ─┬─ A1 A2 A3 A4 A5      (integrity — live is on stale code)
     └─ B1 ──► B2/B3        (~$3; decides deep's fate in production)
        │
        ├─ C1               (universe 120 — do with the A1 redeploy, same restart)
        ├─ C2 ──► C3/C4     (cutoff gate, then the unexamined window)
        ├─ D1 D2            (pre-register + costs, before the forward book fills)
        └─ E1               (the learning loop, on trustworthy grades)

~2026-08-10 ── re-grade monday_t1_pit for free: 9 of 12 cohorts mature, same frozen criteria
```

## 6. What this plan deliberately does not do

- **No new features on the burned window.** 15 ids there already; the answer to a weak result
  is more independent periods, not another variant.
- **No cutover decisions on `monday_t1_pit`.** It was inconclusive; live stays on the current
  schedule under `open_t1`.
- **No claim of edge.** Acceptance §7 (≥150–300 trades, ≥2 regimes, monotone calibration, CI
  excluding zero net of costs) is unmet on every axis, and saying so is the point.
