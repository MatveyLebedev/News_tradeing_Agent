# Pre-registration — profit vs holding time, long-term agent, Monday midday entry (2026-07-29)

**Hypothesis.** A position the long-term agent opens on a **Monday inside the regular
session** (decision stamped 12:00 ET, filled one minute later — its assumed working time)
has a mean profit that depends on how long it is held, and there is a holding time at which
that profit is reliably above zero.

Harness: `longterm_agent/holding_curve_lt.py` (curve) + `longterm_agent/intraday_entry.py`
(fill convention). Entries come from a **new** sweep of the pipeline at a midday `as_of`;
no existing `backtest_id` can supply them (§1.4).

---

## 0. Status — registered BEFORE the run

Nothing in §3 has been executed. This is the difference from
`PREREG_HOLDING_CURVE_2026-07-28.md`, whose numbers were burned by building and running the
harness at the same time. Here the only things that have touched data are the feasibility
census in §1 and a 9-trade **plumbing** check on arbitrary made-up directions (used to
verify SQL, the grid seam, and the benchmark — it produced no reading and its directions
were not the agent's).

One inherited burn is real and is stated up front: the **price paths** of cohorts
2026-04-27 and 2026-05-04 have been mined in detail by
`docs/FAILURE_MODES_monday_t1_pit.md` (all 30 trades' outcomes examined). The midday clock
will select a *different* set of trades, but the tape those two Mondays printed is not
fresh. §3.6 pre-registers the leave-those-out reading for that reason.

---

## 1. Feasibility — measured 2026-07-29, not assumed

### 1.1 Prices — everything the experiment needs exists

| | |
|---|---|
| minute bars | `historical_data_*`, **121 tables**, tz-naive ET, 390 bars/day 09:30–15:59 |
| coverage | 2026-02-23 → 2026-07-28, ~107 sessions; **all 64 live-universe tickers present, none missing** |
| daily bars | `stock_prices_daily`, 132 tickers, 69,607 rows, 2024-06-17 → 2026-07-28 |
| SPY | daily only (529 rows). No minute table — see §1.1a |
| news | 36,070 rows; dense from 2026-02 (879 → Mar 6,549 → Apr 8,061 → Jul 5,709) |

The seam between the two price legs was measured on the plumbing check: the 16:00 minute
print vs the official daily close differs by a median of **0.027%** (max 0.067%). The legs
are on the same price scale.

**The missing ETF constrains one afternoon, not the experiment.** With no SPY minute table
the 238 minutes between the fill and the entry-day close cannot be benchmarked against a
traded instrument, so that stub uses an equal-weight index of the sweep's own 64 names. From
the entry day's close onward SPY exists and is used, exactly as the ledger's daily books do
— see §2.4 for how the two legs chain.

### 1.1a The missing ETF — searched, confirmed, and fixed going forward

Searched exhaustively on 2026-07-29 before accepting the workaround:

- no table anywhere in the schema with `spy` in its name;
- no other intraday-shaped table (`%minute%`, `%intraday%`, `%bar%`, `%tick%`) exists;
- all **121** minute tables are single names — **zero ETFs of any kind**;
- meanwhile the DAILY side has **SPY plus all 11 sector ETFs** (XLB XLC XLY XLP XLE XLF
  XLV XLI XLRE XLK XLU).

Root cause found: `bloomberg_news_fetcher/price_fetcher.py` fetched `TOP_50_TICKERS`, the
*company* list, while `daily_price_fetcher.py` had long since split out a separate
`BENCHMARK_TICKERS` and fetched both. The intraday fetcher never got that split, so the
ETFs were never requested. **Fixed** — the minute fetcher now mirrors the daily one
(`DEFAULT_TICKERS = TOP_50_TICKERS + BENCHMARK_TICKERS`), with the benchmark list kept out
of `TOP_50_TICKERS` because that list also feeds `LongTermAgentConfig.get_universe()` and
the agent must never be asked for a news background on an ETF (verified: no ETF leaks into
the universe).

**This does not rescue the present experiment.** Yahoo caps 1-minute history at ~30 days,
8 days per request — its own error text says so, and the boundary was probed directly:

| requested window | result |
|---|---|
| 2026-07-01 → 07-06 | 780 bars ✅ |
| 2026-06-29 → 07-04 | empty ❌ |
| 2026-04-13 → 04-18 (cohort window) | empty ❌ |

So of the 16 cohorts, only **2026-07-06, 07-13, 07-20 and 07-27** are still retrievable at
all, and one more ages out every day. April–June is gone from this source permanently.
Recovering it needs a vendor with intraday history — FMP is wired here for earnings on the
free tier only, and no Polygon/Alpaca key is configured.

**Consequence for §2.4:** for the entry-day stub the equal-weight index is not a shortcut
around a missing file — it is the only market factor that can cover those minutes in this
window. Everything from the entry close onward already has SPY on daily bars, so only the
stub is synthetic. A fully SPY-benchmarked repeat, stub included, becomes possible for
experiments run from ~August 2026 onward.

### 1.2 The cohort grid — 15 usable Mondays, and one of them is a trap

Earliest viable cohort is set by the **news corpus**, not by prices. `LT_LOOKBACK_DAYS=60`
reaches back 60 days, and news only becomes dense on 2026-02-23. Measured `NO_DATA` rates
on the 30-ticker universe across existing sweeps:

| cohort | 03-16 | 03-23 | 03-30 | 04-06 | 04-13 | 04-20 |
|---|---:|---:|---:|---:|---:|---:|
| NO_DATA / 30 | 16 | 13 | 12 | 1 | **0** | **0** |

→ corpus-wise 2026-04-13 would be viable. **The burn map moves the start to 2026-04-27**
(§1.2c): 04-13 and 04-20 were run on every live name by the March holdout family, so they
carry no fresh cell at all.

Caveat: that table was measured on the **30**-name core. The book (§1.2b) is thinner
on news, so its April cohorts may carry more `NO_DATA` than this suggests — the direct
measurement on the wider universe (§1.2b) exists only for July. This costs candidates, not
correctness: a `NO_DATA` row produces no signal and never enters the book.

**2026-05-25 is Memorial Day: zero minute bars, and it IS on the Monday grid** (the existing
`monday_t1_pit` sweep ran a cohort there). A midday in-session entry on that date is not a
thin sample, it is an impossible trade.

**Rule: a cohort on a closed market rolls forward to the next session, same time of day**
(`backtest.py --roll-holidays`; verified on the real calendar: 2026-05-25 → **2026-05-26**,
every other Monday unmoved). Rolling beats skipping on a book this block-starved — a
skipped holiday is a lost block, a rolled one is a real tradable decision that happens on
Tuesday, still inside its own news week. The roll refuses gaps longer than 5 days, because
past that it would move the decision into a different news background. The curve harness
independently drops any cohort with no session, so a sweep run without the flag still
cannot fill on a holiday.

Grid: **2026-04-27 → 2026-07-27, 14 Mondays (05-25 rolled to Tuesday). Book = 34 low-burn
names × 14 = 476 runs. See §1.2b/§1.2c.**

### 1.2c Burn map — burn is three-level, not binary

Computed over every `(ticker, as_of)` in `backtest_runs` on the 14 candidate cohorts.
Counting *runs* alone is the wrong metric: a prior sweep in which a name came back NEUTRAL
leaked almost nothing, while a traded-and-graded cell had its outcome examined.

| level | names | cells used /14 | distinct sweeps | trades per name | graded |
|---|---:|---:|---:|---:|---:|
| **untouched** | 21 | 0 | 0 | 0 | 0 |
| **lightly touched** | 13 | 7 | **1** (`wf_extra13`) | 0–2 | 7 |
| heavily burned | 30 | 12 | **23** | up to 8 | 19 |

The gap between the middle and top level is the whole point: one sweep against
twenty-three. Worst of all are NVDA and CRM — 8 trades each, every one dissected in
`FAILURE_MODES_monday_t1_pit.md`.

Cohorts **2026-04-13 and 04-20** are excluded outright: the March holdout family
(`holdout_mar_*` ∪ `wf_fresh30_holdout` ∪ `wf_extra13`, 72 distinct tickers) ran every live
name on them, so they hold no low-burn cell at all.

### 1.2b Universe — 34 low-burn names, sector-balanced against production

**Grid: 14 Mondays**, 2026-04-27 → 2026-07-27 (05-25 rolled to Tuesday 05-26).
**Book: the untouched 21 + the lightly touched 13 = 34 × 14 = 476 cells.**

```
ABBV BA CAT COST CSCO CVX GE HD IBM JNJ MCD MRK NKE NOW PG RTX T TMUS UNH VZ WFC
ABNB AMAT BRK-B CMCSA COP GOOG INTU LMT MU PANW PYPL SBUX TXN
```

**81% of cells fully untouched**, 19% lightly touched (one sweep, ≤2 trades per name),
**zero heavily burned**. Composition is constant across all 14 cohorts — no calendar drift.
Every name is drawn from the live production universe (snapshot 2026-07-26, 64 names);
nothing invented, no duplicates.

**Why 34 and not the untouched 21.** The 21 alone diverge from production by **26%** of
sector weight; adding the 13 brings that to **15%** — and those 13 are precisely the tech
and financial names the untouched block lacks, because prior sweeps burned exactly what
production is heaviest in.

| sector | book (34) | live (64) | Δ | untouched-21 alone |
|---|---:|---:|---:|---:|
| Technology | 24% | 30% | −6 | 14% |
| Financial Services | 9% | 16% | −7 | 5% |
| Industrials | 15% | 8% | +7 | 19% |
| Communication Svcs | 15% | 14% | +1 | 14% |
| Consumer Cyclical | 15% | 11% | +4 | 14% |
| Healthcare | 12% | 9% | +2 | 19% |
| Consumer Defensive | 6% | 8% | −2 | 10% |
| Energy | 6% | 5% | +1 | 5% |

Residual tilt is honest and small: financials light by 7pp (prior sweeps burned nearly all
of them), industrials heavy by the same.

**Rejected alternative, recorded:** a 34 × 7 book restricted to cohorts where all 34 are
untouched scores a better 15% → but halves the cohort count, and cohorts are the binding
constraint on every CI here (§1.6). Representativeness bought with statistical power is a
bad trade at 6–9 blocks.

**Two production defects, handled differently.** `GOOG` is in the book and `GOOGL` is not,
so production's Alphabet double-count does not reproduce here — luck, not design. But `NOW`
and `COST` are in, and their tickers collide with ordinary English words, so ~6% of the book
carries a partly noise corpus. Kept deliberately: production lives with it, and cleaning it
in backtest would mean measuring something production is not. Robustness arm drops them.

### 1.2a Two data holes, and neither is a half-day

Minute coverage is 390 bars on every session except **2026-03-27** (146–148 bars) and
**2026-06-23** (23 bars) — identical counts across all six probed tickers, so these are
fetcher outages, not short market days. Neither lands on a Monday, but 2026-06-23 is a
roll target, so the session calendar counts bars (`MIN_SESSION_BARS = 200`) rather than
trusting that a date exists. The threshold sits below a real US half-day (~210 bars) on
purpose.

### 1.3 Maturity — n falls with the horizon, by exactly this much

Trading days available after each cohort (daily bars end 2026-07-28):

| exit horizon | 5d | 10d (≈2w) | 20d (≈4w) | 30d | 40d | 60d (≈12w) |
|---|---:|---:|---:|---:|---:|---:|
| cohorts with room | 14 | 13 | **11** | 8 | 6 | **3** |

The intraday leg and `eod 0d` keep all 15. The 60-day point rests on **three Mondays** and
is a curiosity, not a measurement.

### 1.4 Why existing sweeps cannot be reused

`monday_t1` / `monday_t1_pit` ran at **06:30 pre-open**. A midday `as_of` moves the news
cutoff forward 5.5 hours, so the corpus, the shift metrics, and therefore the signals all
differ. The decisions must be generated fresh. Two mechanical consequences were found and
fixed as part of this preparation:

- `backtest_runs.as_of` is a **DATE**, so a cohort's time of day was never recorded. It is
  now stamped as `provenance.cohort_time_et`, and the curve harness **refuses to run**
  against a sweep that lacks it rather than assuming noon.
- `bucket_summary_cache` is keyed `(ticker, week_start, version)` with `week_start` a DATE.
  A 12:00 sweep and an 06:30 sweep of the same Mondays have bucket boundaries 5.5h apart
  but **collide on that key**, so the midday run would silently read the pre-open run's
  weekly summaries. `BUCKET_CACHE_VERSION` is now env-overridable; the sweep must namespace
  it (§3, `LT_BUCKET_CACHE_VERSION=0.1.0-mid1200`).

### 1.5 The 1-minute agent time is the premise, and it is ~2× optimistic

Unlike the intraday experiment there is **no feed delay term here** — the long-term agent is
not triggered by a headline, it runs on a schedule at `as_of`. The only latency is the
pipeline's own runtime, and that is measured:

| clock | source | p50 | p90 | max |
|---|---|---:|---:|---:|
| long-term pipeline runtime | `longterm_agent_results.execution_time_seconds` (n=149, 2026-06-15 → 07-26) | **121.1s** | 277.6s | 510.3s |

So "1 minute" fills at 12:02 while the median live run would fill at **12:03**, and a p90
run at **12:06**. This shifts the level, not the premise; it is registered as a robustness
arm (§3.5), not as a reason to change the primary reading the hypothesis asks for.

### 1.6 Power — the binding constraint, stated before the run

The yield of directional, risk-gated signals on the comparable pre-open grid
(`monday_t1_pit`, 12 Mondays):

```
04-27: 15   05-04: 14   05-11: 0   05-18: 1   05-25: 0   06-01: 0
06-08:  0   06-15:  0   06-22: 0   06-29: 6   07-06: 4   07-13: 1
                                                       total 41
```

**29 of 41 trades sit on two Mondays, and five consecutive Mondays produced none.** The
effective sample is closer to **5 blocks than to 12**, and 16 cohorts will not fix that.

Scaled to the book (34 names × 14 cohorts): in `sunday_sim` **6 of 9** traded names came
from these 34, i.e. ≈3 trades per cohort, so expect **≈35–45 trades**. The count is not the
constraint — burstiness comes from the detector going quiet across a whole week rather than
from having too few names, so expect only **~6–9 non-empty cohorts**, and every CI here is
set by those, not by the trade count.

With that, the cohort-block CI on the mean will be **wide — of order ±2–5pp at the
multi-week horizons**, where per-trade σ ran 10%+ in the `monday_t1_pit` book. An
inconclusive result is the *likely* outcome and must be reported as inconclusive — not as
"no edge", and not as a shape read off a curve whose band contains zero throughout.

**Diagnosis — the silent weeks are the DETECTOR, not the gate.** On 05-25 → 06-22 the shift
detector fired on 0–1 of 30 tickers and **max conviction across all 30 was 0.20–0.25**,
against a 0.55 trade gate:

| cohort | 04-27 | 05-04 | 05-11 | 05-18 | 05-25 | 06-01 | 06-08 | 06-15 | 06-22 | 06-29 | 07-06 | 07-13 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| shift_flag /30 | 26 | 21 | 3 | 1 | 0 | 1 | 1 | 1 | 0 | 8 | 9 | 3 |
| max conviction | .78 | .82 | .50 | .62 | .20 | .25 | .25 | .25 | .25 | .72 | .80 | .55 |

The pipeline did not *reject* candidates in those weeks — it found none. **Lowering the
gate cannot recover a single trade there**, which rules out the cheapest-looking fix and
redirects the power question to §6.

---

## 2. Design

### 2.1 Entry — fill convention `mid_1m`

Decision at `as_of` (Monday 12:00 ET) + `--agent-minutes`; fill at the **open of the first
minute bar that starts STRICTLY AFTER** the decision. 12:00 + 1m → the **12:02** open, not
12:01: a bar whose open prints at the same instant the agent finishes is a race the
backtest would always win. `--at-or-after` reproduces the looser convention
`profit_compute/holding_curve.py` uses, and exists only so the difference is measurable.

Entry must land on a **real** bar; the search is forward-only within `--entry-tolerance`
minutes. A fill that would precede its own decision is dropped, not repaired.

**12:00 is a config choice, not a swept parameter.** Moving the decision time after seeing a
curve turns time-of-day into an extra horizon. If a different midday is wanted, it is a new
`backtest_id` and a new pre-registration.

### 2.2 Exits — two legs, one axis

| leg | grid | price source |
|---|---|---|
| `intraday(h)` | 1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240 minutes | entry day's minute bars |
| `eod(k)` | 0, 1, 2, 3, 4, 5, 10, 15, 20, 30, 40, 60 **trading days** | `stock_prices_daily` close |

Both map onto **session minutes held**: `intraday(h) = h`; `eod(k) = (390 − entry_point) +
390k`. The overnight gap counts as **zero** because it cannot be exited through — using wall
clock would put the two legs on incomparable axes. The legs meet exactly at `eod(0)`, and
that seam is printed as a data check (§1.1), not hidden.

`eod` horizons are trading days; the ledger's 2w/4w/12w land at k ≈ **10 / 20 / 60**.

**Same trades at every horizon.** Only the exit moves along the x axis.

### 2.3 Truncation — the knob that can manufacture a shape

| mode | what it does | what it costs |
|---|---|---|
| `drop` (default) | the trade leaves the sample when its exit does not exist | n falls with h; late cohorts drop out, composition drifts |
| `clamp` | exit at the last price the series reaches | constant n, tail flattened **by construction** |
| `balanced` | only trades with room for the longest horizon | composition fixed, n collapses (3 cohorts at 60d) |

Report `drop`; quote `clamp` and `balanced` as robustness. A curve that appears in only one
mode is an artefact of that mode.

### 2.4 Benchmark — SPY where a real ETF exists, equal-weight only where none can

`--benchmark spy` (**default, primary**). The trade's excess is chained across the two legs:

```
fill 12:02 ─────── 16:00 ──────────────── close of trading day k
     equal-weight stub        SPY daily closes
     (238 minutes)            (the ledger's own benchmark)
```

The entry-day stub is equal-weight because **nothing else can price those 238 minutes** —
no ETF minute table covers this window (§1.1a). From the entry day's close onward the
benchmark is the real SPY, exactly as every daily book in `BACKTEST_LEDGER.md` is graded.
So the synthetic part is confined to one afternoon, while the multi-week leg that carries
essentially all of the variance is measured against a traded instrument.

Chaining rather than close-anchoring is deliberate. Anchoring **both** sides at Monday's
close would drop the fill → 16:00 stub from the P&L entirely — and with it the midday entry
that is the whole hypothesis. The chain keeps the position's real entry moment and still
puts SPY on the part where SPY is available.

`--benchmark ew`: equal-weight throughout (the full evaluated universe, not the traded
subset — a book that is its own benchmark has zero excess by construction, measured at
0.0 ± 5e-15 on a 3-name check). Registered as the robustness arm: on a plumbing check the
two modes differed by **2.6pp on average at `eod:20`**, so this choice is not cosmetic and
a result that survives only one of them is a result about the benchmark.

`--benchmark none`: raw signed profit, the actual P&L.

The intraday leg is equal-weight adjusted under every mode, for the same reason — the
`benchmark` column records per row which one was actually applied, so this is never
ambiguous in the output. `excess_spy_from_close` remains as a separate column: it anchors
both sides at the close and is therefore directly comparable to the ledger's daily books,
at the cost of not measuring the midday entry.

### 2.5 Costs

`--cost-bps` subtracts one round trip. It shifts the level, not the shape — but the level is
what decides tradability, and at these effect sizes a realistic spread is the same order as
the whole signal.

### 2.6 Statistics

- **Cohort-block bootstrap CI.** Trades inside one Monday share a market week and a news
  background — they are one bet. Ledger F10 measured block CIs at ~5× the per-trade width;
  with this book's concentration (§1.6) a per-trade CI would be fiction.
- **Sign-flip permutation, one coin per cohort** — tests the claim actually made (the
  *direction* carries information), with a null that keeps the data's clumpiness.
- **Always-long control** — direction-blind mean, adjusted identically. If the signed curve
  does not clear it, the agent is selling market drift.

---

## 3. Pre-registered readings

Fixed now, before any sweep:

1. **Primary horizon: `eod(20)`** — ~4 weeks, the agent's own documented horizon and the
   one every ledger entry is scored on. Chosen from the incumbent convention, not a curve.
2. **Primary sample:** `--backtest-id monday_mid1200`, traded (risk-gated) book,
   `--truncation drop`, `--benchmark spy`, `--cost-bps 0`, `--agent-minutes 1`.
3. **Primary statistic:** mean signed profit at `eod(20)` with its cohort-block CI.
4. **Support requires all three:** CI excludes zero · `p_signflip < 0.05` · mean exceeds the
   always-long control. Two of three is not support.
5. **Shape claim** ("there is a best holding time") is decided on the **paired** table, not
   the level curve: for each horizon, the per-trade difference against the reference
   `eod:20`, with its cohort-block CI. The claim requires a horizon whose paired CI
   excludes zero, *and* that this survives in `drop` / `clamp` / `balanced` at the same
   horizon ±1 grid step, *and* under `--agent-minutes 2` (the measured p50 runtime, §1.5).
   The paired test is the better-powered form of the question and is registered as the
   primary route to a shape conclusion — see §6.1.
6. **Burn-aware secondary:** the same reading with
   `--exclude-cohorts 2026-04-27,2026-05-04`. If the result exists only *with* the two
   cohorts whose tape has already been mined, it is not evidence.
7. **Ungated secondary:** `--all-directional`, to separate "the agent's direction is
   informative" from "the risk gate selects well".

**The maximum over the ~26-point grid is a SELECTED maximum**, with a denominator of 26. It
is a hypothesis for a fresh window, never a result. The harness prints this warning next to
the number.

---

## 4. What would falsify

- CI spans zero at `eod(20)` → no detectable edge (the expected outcome at ~6–9 blocks).
- Always-long ≥ signed mean → the direction adds nothing; the tape did the work.
- Curve survives in `clamp` but not `drop` → the shape is the clamping artefact.
- Curve exists at `--agent-minutes 1` and dies at 2 → the experiment is measuring a fill the
  live pipeline cannot achieve, and the work belongs in pipeline latency, not in the exit
  rule.
- Intraday leg shows a strong shape → **suspect the harness, not the market.** A weeks-long
  thesis has no mechanism that pays off in 30 minutes; a large intraday effect is far more
  likely a fill or benchmark bug than a discovery.

---

## 5. Runbook — not executed

**Step 1 — generate the decisions** (476 runs ≈ $15; the only LLM cost).

```bash
LT_LLM_CACHE=true LT_MAX_REFLECTION=0 LT_BUCKET_CACHE_VERSION=0.1.0-mid1200 python3 -m longterm_agent.backtest --backtest-id intraday_curve34 --tickers ABBV BA CAT COST CSCO CVX GE HD IBM JNJ MCD MRK NKE NOW PG RTX T TMUS UNH VZ WFC ABNB AMAT BRK-B CMCSA COP GOOG INTU LMT MU PANW PYPL SBUX TXN --start 2026-04-27 --end 2026-07-27 --step 7 --at 12:00 --roll-holidays --horizons 28 --workers 8 --no-grade
```

⚠ **`--no-grade` is mandatory.** `backtest.py`'s grader fills on daily bars: `--fill d0`
would fill at the as_of day's 09:30 open — *hours before a midday decision exists* — and
`--fill t1` fills at Tuesday's open, which is not this experiment's entry either. The
midday book is graded only by the curve harness.

**Step 2 — the curve** (no LLM cost, re-runnable).

```bash
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --benchmark spy
```

**Step 3 — the pre-registered robustness arms.**

```bash
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --truncation clamp --tag clamp
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --truncation balanced --tag balanced
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --agent-minutes 2 --tag lat_p50
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --exclude-cohorts 2026-04-27,2026-05-04 --tag unburned
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --all-directional --tag ungated
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --cost-bps 10 --tag cost10
python3 -m longterm_agent.holding_curve_lt --backtest-id intraday_curve34 --benchmark ew --tag ew
```

Outputs land next to the module: `holding_curve_lt_<id>[_<tag>]{.png,_trades.csv,_summary.csv}`.

---

## 6. Power — what can actually be done about it

Power is the binding constraint (§1.6) and it is not a data problem: the prices are all
there. Ranked by gain per dollar.

| lever | what it buys | cost | status |
|---|---|---|---|
| **6.1** paired shape test | large — cancels the cohort shock before averaging | free | **implemented**, §3.5 |
| **6.2** holiday roll, not skip | +1 cohort (15 → 16) | free | **implemented** |
| **6.3** variance split, to pick the next lever | tells you whether 6.4 or 6.5 is the right spend | free | **implemented** |
| **6.4** widen the universe 30 → 64 (live) | ≈1.3× trades; also makes the result about the production book | 2.1× LLM (480 → 1024 runs) | **taken** — §1.2b |
| **6.5** forward accrual | +1 genuinely out-of-sample cohort per week | free, slow | ongoing |
| **6.6** all-weekday companion | more trades per week, **no new blocks** | 5× LLM | not recommended first |
| **6.7** lower the trade gate | **nothing** — see §1.6 | free | ruled out by measurement |
| **6.8** extend backwards before 2026-04-06 | more blocks | needs a news backfill | blocked |

**6.1 — pair the horizons.** The level question ("is mean profit above zero at 4w") is
fought against the entire cohort-level shock: one bad Monday moves it, and with ~6–9 blocks
there is no beating that. But the question the experiment actually asks is a **shape**
question, and shape is a *within-trade* comparison — both exits ride the same market move,
so it cancels in the difference before any averaging. `holding_curve_lt.py` now prints, for
every horizon, the per-trade difference against `eod:20` with its own cohort-block CI. On a
synthetic book with an ±8pp per-Monday shock and a true +0.5pp edge, the level CI cannot see
it and the paired CI is >10× narrower and excludes zero (`test_paired_test_is_narrower…`).
This is the single largest power gain available and it costs nothing.

**6.3 — let the data choose the next spend.** The harness prints the between-cohort vs
within-cohort variance split at the primary horizon. If variance is mostly **between**
Mondays, only more Mondays help and 6.4 is wasted money. If it is mostly **within**, a wider
ticker universe narrows the CI without waiting weeks. This cannot be answered before the
sweep, so it is registered as the decision rule rather than guessed at now.

**6.4 — taken: the sweep runs the live 64.** The universe is production's own (§1.2b), so
the result speaks about the book that actually trades rather than a subset of it. Measured
gain is ≈1.3× trades for 2.1× the spend — real but sublinear, because the added names are
thinner on news, and because the silent weeks are silent for the detector rather than for
lack of candidates.

The next rung would be the 120-name `TOP_50_TICKERS` list, and it is **not** proposed here:
it needs the `get_universe()` ImportError fixed first (§1.2b), doubles the spend again, and
reaches names thinner still — median 60-day corpus **16 articles against 39** for the
long-standing core. Whether it is worth anything is exactly what 6.3's variance split
answers after this sweep, so it waits for that number.

*Superseded reasoning, kept for the record:* an earlier draft proposed 30 → 48 by taking
every priced ticker clearing the 8-article floor. That path had to strike `GOOG` (the same
company as `GOOGL`) and `NOW`/`LOW`/`COST`, whose tickers collide with ordinary English
words and whose "corpora" are dominated by headlines like *"Now, it's going for Wall
Street"* and *"the cost of the Iran war"*. The live list carries `GOOG`, `NOW` and `COST`
anyway — production has the same contamination, and §1.2b registers it rather than hiding
it. The silent-week argument was that more names are more chances a quiet week is not
quiet.

**6.6 — why not just run every weekday.** It multiplies trades inside the weeks that are
already active, but the blocks stay weekly (adjacent days share a news week), and the five
silent weeks were silent for the whole week, not just Monday. It buys precision where there
is already data and nothing where the gap is. It also stops being the registered hypothesis.

Residual limits that no sample size fixes:

- **One regime.** Everything is Apr–Jul 2026, war-dominated
  (`REACTION_NEWS_DATASET_2026-07-27.md` §6). No split inside it is worth anything.
- **No intraday ETF for THIS window.** The fetcher is fixed (§1.1a) and SPY minute bars now
  accrue, but the 30-day Yahoo cap means the cohort window cannot be backfilled. A
  SPY-benchmarked repeat of this experiment is possible from ~August 2026 onward, and is the
  clean way to check whether the equal-weight index biased anything here.
- **A measured agent clock.** `execution_time_seconds` exists per run; a sweep could use each
  cohort's own runtime instead of a flat assumption, exactly as `holding_curve.py
  --latency-mode runtime` does for the intraday agent.
- **Costs and fills.** Zero-cost, fill-at-the-open is an upper bound; at these effect sizes a
  realistic spread can consume the entire result.
