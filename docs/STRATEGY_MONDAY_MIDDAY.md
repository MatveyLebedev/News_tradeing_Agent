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

### 2.1 What "in session" means — corrected 2026-08-10

`in_session` originally read **only the clock**, so it answered TRUE for a Sunday 12:40. A
weekend batch that overran into those hours would then be treated as an in-session decision
that can never fill — the market was shut, no minute bar exists, and `resolve_entry` returns
None by design ("pending, never a fallback"), so the row sits PENDING for good. It now tests
the weekday first.

Holidays are handled one level up, because they need the price table: when an in-session
decision finds no minute print, `resolve_entry` asks `market_traded_on` whether the market
opened **at all** that day. If it did not — a weekday holiday, and Monday cohorts are exposed
to several — the daily convention below is the correct answer, not a fallback. If it did, the
row stays PENDING, because then the missing print is a data hole or a halt in one ticker and
must not quietly change convention.

The frozen rule itself is unchanged: fill at the open of the first minute bar strictly after
the decision. What changed is that a closed market is no longer called a session.

## 3. Preconditions — met 2026-08-12, and the schedule moved

**The live schedule is now `monday 12:00 ET`** (`LT_BATCH_WEEKDAY=monday`,
`LT_BATCH_TIME=12:00` in `/root/trade_agent/.env`). First Monday cohort: **2026-08-17**.
Until then the last Sunday-06:00 cohort (`as_of 2026-08-09`) remains the newest, and every
cohort written before the switch keeps `open_t1` — the grader reads each row's own clock,
so nothing already in the table is reinterpreted.

Both legs must share a convention: a stock filled at 12:02 against a benchmark filled at
09:30 measures the two over different windows and books the gap as skill. That is what the
two blockers below were about.

1. ~~**SPY has no minute table.**~~ **Closed 2026-08-12.** The `BENCHMARK_TICKERS` fix
   shipped in `stock-fetcher:2026-08-11`; the fetcher now reports `132/132 tickers`
   (120 companies + 12 ETFs) and SPY/XLK/XLRE carry live current-session bars. The
   April–June history that Yahoo could never serve was backfilled from Alpaca
   (`bloomberg_news_fetcher/alpaca_backfill.py`). Measured effect on the first grading run
   after the fix: `pending_data` went 9 → 0.
2. **The nightly repair sweep still writes nothing.** `auto_price_fetcher.py` is not even
   copied into the fetcher image (`Dockerfile.news` lists its `COPY` sources by name), so
   the hole-repair path is absent in production. The 5-minute live fetch makes holes rarer
   but does not remove the need: Yahoo serves ~30 days of minute history, so a hole not
   repaired inside that window is permanent. **This one is still open.**

Stock minute bars themselves are healthy: 120 of 121 tickers current, full 391-bar sessions
(`historical_data_sq` is empty — Block renamed SQ→XYZ, and XYZ is backfilled).

### 3.1 What the switch does and does not change

* **Only new cohorts.** `resolve_entry` picks the convention from each row's own
  `run_timestamp`, so 2026-08-17 onward gets `mid_1m` and everything earlier keeps
  `open_t1`. Mixed conventions across cohorts are expected and must be split when analysing.
* **`session_guard` becomes live.** Out of session it was inert. From a 12:00 start it stops
  the batch rather than let it cross 16:00, because one cohort holding two conventions is
  corrupt data wearing the shape of good data. Budget: `PER_TICKER_MINUTES = 2.0` × 64 =
  ~128 min, projected finish ~14:08, against measured runs of 93–101 min. An incomplete
  cohort is the intended failure mode.
* **Freshness preflight still passes.** Thresholds are 2 weekdays on `prices`/`prices_spy`;
  at Monday noon the newest daily bar is Friday's close — one weekday behind.
* **The evidence did not move.** §4.1 still stands: two of three pre-registered conditions,
  not three. This migration makes the live path *measurable under the frozen convention*;
  it is not a claim that the convention earns more.

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

**And the head-to-head does not exist — that is an absence, not a negative result.** Recorded
2026-08-10, because the sentence above is easy to read as "we compared and it did not win".
Nothing was compared. `cohort_time_et` is stamped on three books only (`monday_mid1200`,
`monday_mid1200_smoke`, `intraday_curve34`), all at 12:00; every other book is pre-open or
weekend. `monday_t1_pit` starts on the same day but on 30 burned tickers under a different
entry convention, so it is not a control.

What `intraday_curve34` answered is the **moment of execution** given a decision: the
intraday leg is flat from one minute to the close (−0.006%…+0.056%, every CI covering zero),
so delaying a fill by hours inside the session changes nothing. What it did not answer is the
**moment of decision** — an agent run at 06:30 Sunday and one run at 12:00 Monday read
different news. Testing that needs a paired sweep over the same 34 names and the same 14
Mondays at `--at 06:30`; ~476 runs, roughly $13. Until it is run, moving the live schedule to
Monday midday is an **unmeasured change**, adopted on the same execution-safety grounds as
the convention itself.

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
