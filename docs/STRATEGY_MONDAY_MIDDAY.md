# Strategy: Monday midday entry (`mid_1m`)

Status: **frozen**, 2026-08-05. This is the entry convention every long-term book uses from
this date. It is the reference every later experiment varies against, so a change here needs
its own pre-registration and a new document, not an edit to this one.

Chosen by judgement, **not** by a measured edge — see §4. Read that section before citing
this document as evidence of anything.

---

## 1. The rule

> The agent runs on **Monday inside the regular session**. The position fills at the **OPEN
> of the first one-minute bar that starts STRICTLY AFTER the decision exists**. If Monday is
> a market holiday, the cohort rolls forward to the next session (Tuesday).

* **Decision moment** = `longterm_agent_results.run_timestamp`, stamped `NOW()` when the row
  is persisted — i.e. *after* the pipeline finished. Its runtime is already inside the
  anchor, so no "agent working time" is added on the live path (`latency_minutes = 0`).
  A broker-side routing delay, once measured, belongs in `latency_minutes` and nowhere else.
* **Strictly after, not at-or-after.** A bar stamped 12:02 covers 12:02:00–12:02:59; filling
  a 12:02:00 decision there buys at a price printed in the minute the decision was made.
* **Exit** stays daily: horizon anchors at the entry date, exit at the close on or before
  `entry_date + horizon`. Only the entry moved to minute resolution.
* **Horizon: 20 trading days (~4 weeks).** Unchanged, and see §4.2 for why it did not move.
* **Scoring: beta-matched excess vs SPY** — `docs/BENCHMARK_CONVENTION.md`.

### Why not the daily conventions

| convention | what it would do to a Monday-noon decision |
|---|---|
| `open_t1` | buys the **next** session's 09:30 open — ~21h late, the rest of Monday discarded |
| `open_d0` | buys **Monday's** 09:30 open — 2.5h *before* the decision existed: look-ahead |
| `mid_1m` | buys the first minute bar after the decision — the only one that is both tradable and PIT-clean |

## 2. Where it is implemented

| surface | how |
|---|---|
| live grading | `longterm_agent/exit_rules.py::resolve_entry` → `entry_bar_mid`; `grade_results.py` passes `run_timestamp` |
| stop scanner | `grade_results.py::scan_open_breaches` — same resolver, so the stop starts at the price the position paid |
| research harness | `longterm_agent/intraday_entry.py` (the sweep/curve side) |
| agreement | `longterm_agent/tests/test_mid_fill.py::TestAgreesWithResearchHarness` pins the two to the same fill minute |

`resolve_entry` picks the convention **per row from the decision's own clock**: in-session →
`mid_1m`, anything else → the daily rule it already had. No flag, and every row already in
the table keeps its convention.

## 3. Preconditions that are NOT yet met

**The live path cannot grade an in-session row today.** Both legs must share a convention —
a stock filled at 12:02 against a benchmark filled at 09:30 measures the two over different
windows and books the gap as skill. So:

1. **SPY has no minute table.** 0 of 12 ETFs do. The fix exists in
   `bloomberg_news_fetcher/price_fetcher.py` (`BENCHMARK_TICKERS`) and
   `auto_news_fetcher.py` (the call site passed `TOP_50_TICKERS`, so ETFs were never
   requested) — **not deployed**. Until it ships, in-session rows stay PENDING and
   `grade_results` prints how many.
2. **The nightly repair sweep writes nothing.** `auto_price_fetcher.py` targeted a `date`
   column the minute tables do not have. Fixed in the repo, **not deployed**. Yahoo serves
   ~30 days of minute history, so a hole not repaired inside that window is permanent.

Stock minute bars themselves are healthy: 120 of 121 tickers current, full 391-bar sessions
(`historical_data_sq` is empty).

## 4. What the evidence does and does not say

### 4.1 It does not support this change

`intraday_curve34` (14 Monday cohorts, 2026-04-27 → 2026-07-27, 34 low-burn names, 476 cells,
121 trades) pre-registered three conditions at `eod:20` and required all three:

| condition | result | |
|---|---|:--:|
| cohort-block CI excludes zero | [+0.61, +2.97] | ✓ |
| `p_signflip` < 0.05 | 0.031 | ✓ |
| mean beats the always-long control | +1.82 vs +2.25 | ✗ |

Two of three is not support. No arm of the robustness set takes all three, and the
burn-aware arm (dropping the two already-mined cohorts) fails all three.

**Inside the session the curve is flat**: every intraday horizon from 1 minute to the close
lands between −0.006% and +0.056%, every CI covers zero, minimum `p_signflip` 0.155. There is
no measured intraday shape to optimise, and per the pre-registration a strong one would have
implicated the harness rather than the market.

The change is frozen because it is judged **more correct and safer** — a fill inside a liquid
session, no overnight gap between decision and entry — not because it earned a number.

### 4.2 Why the horizon stayed at 20 days

`eod:15` takes all three conditions on the primary reading and holds in `clamp`, `min2` and
`balanced`. It is **not** adopted: it fails the burn-aware arm on all three, dies at 10bps of
cost (p 0.054), and was selected out of ~25 grid points *after* the registered horizon
failed. The paired test — the registered route to any shape conclusion — cannot separate 10,
15, 20, 30 or 40 days. And the gap between 15 and 20 sits in the *control* (0.70 → 1.73), not
the strategy (1.31 → 1.71): it describes the tape, not the agent.

### 4.3 Two findings this experiment produced, neither actionable yet

* **Selection carries nothing measurable.** Long the agent's picks +2.25% vs long the whole
  34-name universe +2.36%; paired premium **−0.14%**, CI [−1.56, +1.69], 3 of 10 cohorts
  positive. The control's return is book composition, not stock picking.
* **Direction ranks correctly but the short leg is mis-calibrated in level.** Names called
  LONG +1.28% vs the universe, names called SHORT −1.45%; spread 3.68pp. The names it wants
  to short still beat beta-matched SPY by +0.43% — they rise *less*, they do not fall.
  Shorting them loses. Post-hoc, 47 and 48 trades, CIs covering zero: a hypothesis for a
  fresh window, and under the burned-grid protocol it can only demote, never accept.

* The regime explanation for the short leg does **not** hold: correlation between the
  universe's beta-matched return and the short side's return is **−0.00** across 10 cohorts.

### 4.4 A defect in the registered criteria, to fix in the NEXT pre-registration

The always-long control is long-only and carries the universe's beta-matched drift (+2.36%
here); a long/short book is neutral to that drift by construction. So condition 3 penalises a
market-neutral book whenever that drift is positive, regardless of the direction call's
quality. The criterion was registered in advance and it failed — that stands, and it is not
retroactively excused. The correct control for the next registration is *long the whole
universe*, not *long the picks*. The registered test of whether direction carries information
is the sign-flip permutation, and it passes (p = 0.031).

## 5. Deviations recorded from the `intraday_curve34` run

1. **Book 3× larger than registered.** 121 trades in 14 non-empty cohorts against a
   registered expectation of 35–45 in 6–9. The power forecast was wrong — in the safe
   direction, but wrong.
2. **17 of 476 cells lost** to Postgres connection drops, repaired by a second pass.
3. **Two provenance stamps**, differing only in `git_revision` (commits landed mid-sweep).
   Knobs, models, temperature identical.
4. **The `balanced` arm is inoperable as registered**: the default intraday grid's top point
   is 240 minutes and a 12:02 fill leaves 238, so every trade was dropped. Re-run with the
   grid capped at 180.

## 6. Reproducing

```bash
# sweep (LLM cost)
LT_LLM_CACHE=true LT_MAX_REFLECTION=0 LT_BUCKET_CACHE_VERSION=0.1.0-mid1200 \
python3 -u -m longterm_agent.backtest --backtest-id intraday_curve34 \
  --start 2026-04-27 --end 2026-07-27 --step 7 --at 12:00 --roll-holidays \
  --horizons 28 --workers 8 --no-grade

# curve (free, re-runnable; beta-matched by default)
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --benchmark spy
```

`--no-grade` is mandatory: the daily grader would fill at 09:30, hours before a midday
decision exists. Full design and burn map: `docs/PREREG_MONDAY_MIDDAY_HOLDING_2026-07-29.md`.
