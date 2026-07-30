# Pre-registration — profit vs holding time for in-session, news-triggered entries (2026-07-28)

**Hypothesis.** A position opened inside the regular session, one minute after the news that
triggered it (the agent's working time), has a mean profit that depends on how long it is
held — and there is a holding time at which that profit is reliably above zero.

Harness: `profit_compute/holding_curve.py`. Reproduce with
`python3 -m profit_compute.holding_curve --source agent`.

---

## 0. Status — read this before the numbers

The harness was **run end-to-end while it was being built**, on both entry universes, to
verify it against the raw bars. Under the burned-grid rule (`BACKTEST_LEDGER.md`,
anti-overfit protocol §1) those numbers are therefore **exploratory, not a test**: this
sample can now only reject the hypothesis, never confirm it. §6 says what an acceptance-
capable run would require.

What is pre-registered here is the *decision rule* — so that when a clean entry set exists,
the reading is not chosen after seeing the curve.

---

## 1. Feasibility — what the data actually supports

| | |
|---|---|
| minute bars | `historical_data_<tkr>`, **120 tickers**, 2026-02-23 → today, tz-naive ET |
| session coverage | 390 bars/day, 09:30–15:59 — **regular hours only**, no premarket, no after-hours |
| ETFs | **none.** No SPY/QQQ/USO/TLT/XLE minute table — the market factor must be synthesised |
| daily bars | `stock_prices_daily`, 132 tickers, 527 sessions — irrelevant here, the test is intraday |
| news | 36,060 rows, dense from 2026-02-23; `published` is naive ET |

Everything the experiment needs exists. The binding constraint is not price data — it is
**how few entries there are**.

### Entry universe A — the agent's own signals (`--source agent`)

```
324 signals in agent_results (signals_generated > 0)
 −221  ticker has no minute table   (USO, SPY, TLT, XLE, QQQ … — 159 tickers, mostly ETFs)
 − 26  published outside 09:30–16:00
 −  2  same (news, ticker) re-analysed by a second agent version
=  75  trades · 12 days · 21 tickers · 2026-03-09 → 2026-06-01 · 45 long / 30 short
```

**36 of the 75 trades (48%) are on one day, 2026-03-09**, and XOM+CVX are 22 of them — the
oil complex in the war regime that `REACTION_NEWS_DATASET_2026-07-27.md` §6 already flagged
as 39% of the corpus. Only 8 trades carry confidence ≥ 0.8. Effective sample size is closer
to **12 (days)** than to 75.

### Entry universe B — reaction headlines (`--source reaction`)

Direction read off the headline verb via `scripts/scan_reaction_news.classify`:
**57 trades · 42 days · 21 tickers**, max 4 trades on any one day. Smaller n, far better
spread — and per `REACTION_NEWS_DATASET_2026-07-27.md` §4 its true curve should be **flat**,
because the article is downstream of the move. That makes B the harness's null: if B shows a
strong shape, the harness is wrong, not the market.

### The 1-minute latency is an assumption, and it is wrong by an order of magnitude

No agent was run to produce any of this: the entries are rows already sitting in
`agent_results`, written by batch runs on 2026-03-02 → 2026-06-01 (v0.1.0) and 2026-06-02
(v2.0.0). "Agent working time = 1 minute" was therefore never measured — but both halves of
the real delay *are* recorded, and neither matches:

| clock | column | p50 | p90 | max |
|---|---|---:|---:|---:|
| agent thinking time | `agent_results.execution_time_seconds` | **60.2s** | 115s | 169s |
| **feed delay** — RSS poll to first sight | `news.fetch_time − news.published` | **9.8 min** | 20.1 min | 156 min |
| live total | sum of the two | **≈10.5 min** | ≈16.8 min | 37.3 min |

(measured on the 75-trade set; 71 of 140 signal-producing runs took longer than 60s)

So the agent's own runtime does sit near a minute — but it never sees the story until the
fetcher's next pass, ~10 minutes after publication. **The tradable clock starts at
`fetch_time`, not at `published`.** `--latency-mode realistic` uses it, and the difference is
not cosmetic: on the exploratory sample the 30-minute mean falls from +0.073% to +0.004%,
and 2 trades stop existing because the decision now lands after 16:00.

This is a claim about the *feed*, not the agent. Making the agent faster cannot recover those
10 minutes; only a faster news path can.

---

## 2. Design

**Entry.** `published + latency`, rounded **up** to the next bar start; fill at that bar's
`open`. Forward-only: a 10:00:30 story fills at the 10:02 open, never 10:01. This is the one
PIT claim the experiment makes, and it is what `profit_compute/trade_simulator.py` gets
wrong — it takes the nearest bar within ±5 minutes, which can fill at a price printed
*before* the news. Numbers from that script are not comparable to these.

`--latency-mode` selects the clock: `flat` (`--latency-min`, an assumption), `runtime`
(measured agent time), `ingest` (measured feed delay), `realistic` (both — what a live run
would have paid). Every fallback to the flat assumption is counted and printed, so the share
of the sample that is assumption rather than measurement is always visible.

**Exit.** Price point `entry + h` on a 391-point grid (390 bar opens + the 16:00 close), so
holding `h` minutes is exactly `h` grid steps. Gaps are forward-filled for exits (that is
what an exit gets) but an entry must land on a real bar.

**Same trades at every horizon.** Only the exit moves along the x axis.

**Truncation** (`--truncation`) — the knob that can manufacture a shape:

| mode | what it does | what it costs |
|---|---|---|
| `drop` (default) | trade leaves the sample once `entry + h` passes 16:00 | n falls with h; late-day entries drop out, so composition drifts |
| `clamp` | exit at the 16:00 close | constant n, but the tail flattens by construction — every long horizon is the same trade |
| `balanced` | only trades with room for the longest horizon | composition fixed, n collapses (2 trades at 360m on universe A) |

Report `drop`; quote `clamp` and `balanced` as robustness. A curve that only appears in one
mode is an artefact of that mode.

**Costs.** `--cost-bps` (default 0) subtracts one round trip. It shifts the level, not the
shape — but the level is what decides tradability, and at these effect sizes a realistic
spread is the same order as the whole signal.

**Market adjustment.** `--market-adjust` subtracts an equal-weight index built from the 120
priced tickers (aggregated server-side). Necessary because 12 days means the common move is
a large share of the variance, and we have no ETF to net against.

**Statistics.**
- **Day-block bootstrap CI.** Trades inside one day are one bet. Ledger F10 measured
  cohort-block CIs at ~5× the per-trade width; with 48% of the sample on one day, a
  per-trade CI here would be fiction.
- **Sign-flip permutation**, one coin per day — tests the claim actually being made (the
  *direction* carries information), with the null keeping the data's clumpiness.
- **Always-buy control** — direction-blind mean. If the curve does not clear it, the agent
  is selling market drift.

---

## 3. Pre-registered readings

Fixed in advance, before any clean run:

1. **Primary horizon: 30 minutes.** Chosen as the round number nearest the incumbent
   `trade_simulator.py` default of 40m, not from any curve. Everything else is secondary.
2. **Primary sample: universe A, `--latency-mode realistic`, `--truncation drop`,
   `--market-adjust`, `--cost-bps 0`.** `--latency-mode flat --latency-min 1` is reported
   beside it as the *upper bound* — the number a hypothetically instant feed would produce.
   Tradability is decided by the realistic run; the flat run only bounds the opportunity.
3. **Primary statistic: mean signed profit at 30m, with its day-block CI.**
4. **Support requires all three:** CI excludes zero · `p_signflip < 0.05` · mean exceeds the
   always-buy control. Two of three is not support.
5. **Shape claim** ("there is a best holding time") requires the maximum to survive in
   `drop` *and* `clamp` *and* `balanced`, and to sit at the same horizon ±1 grid step.

**The reported maximum over the 18-horizon grid is a selected maximum**, with a denominator
of 18. It is a hypothesis for a fresh window, never a result. The harness prints this
warning next to the number for exactly that reason.

---

## 4. What would falsify

- CI spans zero at the primary horizon → no detectable edge (the expected outcome at n=12
  days; see §5).
- Always-buy ≥ signed mean → the direction is not adding value, the tape is.
- Curve survives in `clamp` but not `drop` → the shape is the close-clamping artefact.
- Universe B shows a strong shape → the harness is broken, since B's content is known to be
  spent before the article exists.

---

## 5. Power — stated before the run, not after

With 12 day-blocks and a per-trade σ of roughly 0.5% at 30m, the day-block CI on the mean is
about ±0.2pp wide. Anything smaller than that is invisible here **whether or not it is real**.
An inconclusive result is the likely outcome and must be reported as inconclusive — not as
"no edge", and certainly not as a shape read off a curve whose band contains zero throughout.

## 6. What would make this acceptance-capable

In order of leverage:

1. **More entries from the agent.** 221 of 324 signals die on the price universe and only 12
   days are covered. Re-running the agent across in-session news for the 120 priced tickers
   over 2026-02-23 → now is the one fix that changes the power arithmetic. Cost is LLM calls.
   Note this is also the only way to get an entry set the harness has not already seen (§0).
2. **A faster news path.** The ~10-minute feed delay is the largest single term in the entry
   clock and it is infrastructure, not modelling — no amount of resampling recovers it. If
   the edge only exists at `flat 1m` and dies at `realistic`, the experiment is measuring the
   fetcher's polling interval, and that is where the work belongs.
3. **Widen the intraday price universe.** 159 signal tickers have no minute table, and the
   ETFs the agent likes most (USO, SPY, TLT, XLE) are all missing. Note yfinance serves 1-minute
   bars for roughly the last 30 days only, so **March–June cannot be backfilled from it** —
   this needs another vendor, or it only ever fixes the data going forward.
4. **A second regime.** Everything here is Feb–Jul 2026, one regime, war-dominated
   (`REACTION_NEWS_DATASET_2026-07-27.md` §6). No walk-forward split inside it is worth
   anything.
5. **Costs and fills.** Zero-cost, fill-at-the-open is an upper bound. At the effect sizes
   this data can resolve, a realistic spread can consume the entire result.
