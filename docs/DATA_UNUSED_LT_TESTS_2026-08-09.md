# Unused data available to long-term-agent tests — audit 2026-08-09

Connecting the paper broker (`longterm_agent/broker`, account created 2026-08-05) brought
in a data surface far wider than the six trading endpoints the code calls. This audit asks
one question: **what data do we now hold, or can now reach for $0, that no long-term-agent
test currently consumes — and which specific test does each piece unblock?**

Everything below was **measured on 2026-08-09** against the live Alpaca API and the live
`trade_agent` database, in the style `alpaca_client.py` already uses for Yahoo's limits —
probed, not read from a vendor page. Entitlements change; re-probe before relying on one.

Companions: [DATABASE_POSTGRES.md](DATABASE_POSTGRES.md) (what exists),
[../DATA_EXPANSION_PLAN.md](../DATA_EXPANSION_PLAN.md) (what we planned to buy),
[PIT_RULES.md](PIT_RULES.md) (what a test is allowed to see).

---

## 0. Summary — ranked by test unblocked per unit of work

**If you only read one thing:** the free price backfills below add **zero** new agent
cohorts — the news corpus is the only gate that binds (§3.2). What *is* newly testable
today, at no cost, is the set of horizons that matured while nobody graded them: a +12w
read across ~20 sweeps, curve34's +4w on 101 traded rows, the live book's +2w (§3.1). New
*decision* periods need the news archive, and 2025 is the cheap first step: 52 cohorts and
the first bear regime the agent would ever have been tested in (§3.3).

**And one thing that is not on the table below,** because it is not about new data at all:
the corpus is complete only from **2026-04** (the graph path switches on there, not in
February), **50.9% of the swept grid is already burned** — 270 cells 13–16 times over — and
the unburned remainder is 82% names the corpus has never fed. Meanwhile the forward
window, the only place acceptance is allowed, is thinning: NO_DATA on a fixed name set has
gone 0–7% → 14.7–17.6% since May, tracking a halving of the event-extraction rate. **That
regression costs more test capacity than any backfill here buys** (§4).

| # | Unused data | Test it unblocks | Effort | Cost |
|---|---|---|---|---|
| 1 | Alpaca **SIP minute bars, 2016-01-04 →** | gives the intraday benchmark the harness says it cannot build; extends `mid_1m` before 2026-02-23; repairs sessions the current book happens to miss (§2.1) | fetcher + backfill | $0 |
| 2 | Alpaca **daily bars 2016 →, `adjustment=all`** | beta/technical windows and analog grading at any archival `as_of` (today `stock_prices_daily` starts 2024-06-17) | one backfill | $0 |
| 3 | Alpaca **news archive 2016 →, with article bodies** | the *binding* constraint on backtest depth — pre-2026-02 cohorts | fetcher + re-embed + detection replay; **ToS check first** | $0 |
| 4 | **Corporate actions** (splits/dividends/mergers) | split-safety for unadjusted minute bars; total-return grading | small table | $0 |
| 5 | **Asset metadata** (`shortable`, `easy_to_borrow`, maintenance margin) | closes `broker_costs.borrow_annual_bps=None`; SHORT-leg feasibility | one call/day | $0 |
| 6 | **Market calendar** (half-days, session bounds) | removes the hardcoded 390-bar session assumption | trivial | $0 |
| 7 | **Fills / activities / portfolio history** | realized slippage vs the frozen flat cost model | forward-only, accrues | $0 |
| 8 | **Quotes & trades (tick)** | measured half-spread per name at the fill minute | on demand | $0 |
| — | Options greeks/IV history | *not* unlocked — see §2.9 | — | — |

---

## 1. What the broker connection actually brought

`longterm_agent/broker/alpaca_client.py` calls six endpoints: `account`, `positions`,
`positions/{sym}`, `orders:by_client_order_id`, `POST orders`, `DELETE orders/{id}`.
Everything else the same key pair opens is untouched.

Measured today, this key pair:

| Endpoint | Result |
|---|---|
| `GET /v2/account` | 200 — equity $100,000, `regt_buying_power` 200k, `multiplier` 4, `shorting_enabled: true` |
| `GET /v2/assets/{sym}` | 200 — `shortable`, `easy_to_borrow`, `marginable`, `fractionable`, `maintenance_margin_requirement` (AAPL 30, **TSLA 50**) |
| `GET /v2/calendar` | 200 — 48 sessions returned for 2026-06-01…08-09, each with `open`/`close`, pre/post `session_open`/`session_close`, `settlement_date` |
| `GET /v2/account/activities` | 200 — fills, dividends, splits as they occur (1 row so far) |
| `GET /v2/account/portfolio/history` | 200 — equity curve, `profit_loss`, `profit_loss_pct` |
| `GET data /v2/stocks/bars` **`feed=sip`**, historical | 200 — **minute bars from 2016-01-04**, complete 390/391-bar sessions, 14 symbols in one request |
| `GET data /v2/stocks/bars` `feed=sip`, **last 20 min** | **403** — `"subscription does not permit querying recent SIP data"` |
| `GET data /v2/stocks/bars` `feed=iex` | 200 — but only from ~2021, and sparse (386–390 RTH bars where SIP has 391) |
| `GET data /v1beta1/news` | 200 — Benzinga, **from 2016**, `content` 2.5–3.6k chars with `include_content=true` |
| `GET data /v1/corporate-actions` | 200 — `cash_dividends`, `forward_splits`, `cash_mergers` with ex/record/payable dates |
| `GET data /v2/stocks/{sym}/quotes` · `/trades` | 200 — historical tick data |
| `GET data /v1beta1/options/snapshots/{sym}` | 200 — but payload is `latestQuote` only: **no greeks, no IV** |

The shape of the entitlement is the useful part: **historical yes, real-time no.** That is
exactly backwards from what a trading account normally wants, and exactly right for a
backtest.

---

## 2. What each piece unblocks

### 2.1 Minute bars — the largest single win

What we hold today (measured):

* `historical_data_<ticker>` — 121 single names, **2026-02-23 → 2026-08-07**, no ETFs at
  all. `historical_data_spy` does not exist; `holding_curve_lt.load_ew_daily` documents
  this and falls back to an equal-weight index *because* of it.
  **UPDATE 2026-08-12 — this hole is closed.** A bulk write on 2026-08-09 (34,710 bars in
  one pass, then daily increments) created SPY + all 11 sector ETF minute tables covering
  **2026-04-01 → 08-12, 92 of 92 sessions complete** (XLRE 34/92, the thin one). Validated
  against Alpaca SIP on 4 sample days: 1,560 bars compared, median difference **0.000 bps**,
  max 0.1 bps, 1,554 exact to the cent. The series is correct. Its *source* is not
  established — 89 sessions of history is far outside Yahoo's documented 30-day window, so
  either that note is wrong or another provider was used; worth pinning down before this
  becomes the benchmark of record.
* Within that window the sessions are not all whole. Per ticker, ~115 sessions, of which
  3–4 are short — and two are badly damaged for **every** ticker checked:
  `2026-06-23` = 23/390 bars, `2026-03-27` = 146/390.
* 22 of the 121 traded `intraday_curve34` entries sit on an incomplete entry session
  (worst: LMT 2026-06-01 = 382, ABNB 2026-05-18 = 383).
  **Measured 2026-08-12, this does NOT reach the book.** The `mid_1m` want-bar is 12:02
  and it is present for **120 of 121** trades; one fills a minute late and nothing is
  dropped. On the intraday exit leg, 4 of 1,573 bar lookups are missing (0.3%), touching
  4 trades. The holes are real but they do not land where the harness reads — an earlier
  draft of this section inferred a slid fill from the session totals; the direct
  measurement contradicts it. The eod leg reads daily closes and is untouched either way.
* Yahoo cannot repair any of it: its 1-minute window is the **last 30 days, forward-only**
  (probed 2026-07-29, recorded in DATABASE_POSTGRES.md). A hole older than 30 days is
  permanent from that source.

What Alpaca returns for the same holes:

| Symbol / day | ours | Alpaca SIP |
|---|---|---|
| AAPL 2026-06-23 | 23 | **390** |
| AAPL 2026-03-27 | 146 | **390** |
| ABNB 2026-05-18 | 383 | 388 |
| LMT 2026-06-01 | 382 | 382 (genuinely thin, not a fetch gap) |

And for the benchmark hole, on the first curve34 Monday (2026-04-27):
SPY 391, QQQ 391, XLK 391, XLE 391, XLF 391, XLV 391, XLI 391, XLY 391, XLP 391, XLU 391,
XLB 390, XLC 391, XLRE 383 — i.e. the whole benchmark set, minute-resolution, for a window
Yahoo can no longer serve. SPY minute bars are there for 2016-01-04 and 2020-03-16 too.

**Tests this unblocks**

1. **Repair the sessions — but the curve34 re-run is no longer urgent.** Measured, the
   damage misses the fill minute on 120 of 121 trades and 99.7% of exit lookups, so the
   registered reading is not resting on bad bars. The repair still earns its place for
   what comes next: the equal-weight benchmark is built from the same minute grids, and
   any extension of the harness to other decision times walks straight into the holes
   (2026-06-23 is 23/390 bars, 2026-03-27 is 146/390 — both unrepairable from Yahoo).
2. **A real intraday benchmark.** `holding_curve_lt` uses an equal-weight index for the
   entry-day leg *only* because no ETF minute table exists, and carries SPY as a
   close-anchored cross-reference. With minute SPY/sector bars the benchmark keeps one
   identity across the 16:00 seam, and the sector-relative version of the curve becomes
   possible at all.
3. **`mid_1m` before 2026-02-23.** The fill convention is currently untestable outside a
   14-Monday window. Ten years of minute bars turn the convention itself (strictly-after,
   `agent_minutes`, `--entry-tolerance`) into something measurable across regimes, using
   *any* entry timestamps — including replayed news events — independent of the LLM cost
   of generating new decisions.
4. **Second-source validation of the fill price.** Twelve curve34 entries priced at
   12:01 ET from both sources: **median |diff| 0.00 bps**, 8 of 12 identical to the cent,
   max 17.9 bps (AMAT 2026-05-04). That is a cheap, repeatable canary — a systematic
   divergence would mean a timezone or adjustment bug, which is precisely the class of
   error that reads as "alpha" in an intraday harness.

Note on adjustment: our minute tables are raw Yahoo output; Alpaca serves
`adjustment=split|all`. Any backfill must pick one convention and record it, or a split
inside the window silently becomes a 10× return (see §2.4).

### 2.2 Daily bars back to 2016

`stock_prices_daily` starts **2024-06-17** (70,663 rows, 132 tickers). Alpaca returns
split+dividend-adjusted daily bars from 2016-01-04 for single names *and* ETFs in one
batched call. This is item D1.1 of the Data Expansion Plan, which currently reads
"yfinance `--period max`" — same result, one API, same rate limit as the minute backfill.

Unblocks: beta estimated on more than a 2-year window; the 180-day technical window at any
archival `as_of`; exit-rule simulation and analog grading for cohorts older than 2024-06.

### 2.3 A news archive with real article bodies

This is the one that touches the **binding constraint**. Measured today, our corpus is
dense only from 2026-02 (2026-02: 879 rows, 2026-03: 6,549, …), which is why every
walk-forward sweep in `backtest_runs` lives inside 2026-04-24 → 2026-07-27 — 14 weekly
cohorts, one regime.

Also measured: `news.full_text` is non-NULL on all 38,985 rows since 2026-02, but only
**42 rows exceed 500 characters**. In practice we hold headlines and feed summaries, not
bodies — consistent with the licensing verdict already taken against scraping.

Alpaca's news endpoint returns, for the same key pair:

* history back to **2016-01-05** (probed at 2016, 2021, 2025 — all populated);
* `content` of 2,521 and 3,610 characters on the two 2021 samples inspected, plus
  `summary`, `url`, `author`, `images`, and a `symbols` array (already ticker-tagged);
* volume for 10 mega-caps over a 7-day window: 208 articles (2021-03) → 407 (2026-06).

**Caveats, and they are the whole decision:**

* **Source mix.** 300/300 articles sampled for June 2026 carry `source: benzinga`. Our
  live corpus is Bloomberg-heavy (15,298 `bloomberg-markets` since 2026-02). This is the
  *same* parity problem the GDELT spike hit — depth yes, parity no. It must be handled the
  same way: `source='benzinga'`, a `corpus_era` flag on cohorts, and results reported both
  pooled and split. The seam is a confound to measure, not to hide.
* **Licensing.** Alpaca's terms govern storing and reusing this content. The Data
  Expansion Plan's ground rule ("every source carries an explicit *may we store it?*
  note") applies — **check before backfilling**, not after.
* **Detection release.** Anything that changes the corpus is a new `DETECTION_VERSION` +
  `BUCKET_CACHE_VERSION` bump, not a config tweak.
* **Survivorship.** Pre-2024 cohorts against today's top-120 need a PIT universe or a
  documented long bias; store `universe_version` on the rows either way.

If the ToS clears, this replaces the conditional ~$90–200 one-shot archive pull (D2b) and
makes the GDELT detour (D2a) unnecessary — Benzinga has bodies, and GDELT has titles.

### 2.4 Corporate actions

Returns `cash_dividends` (rate, ex/record/payable dates), `forward_splits`
(`old_rate`/`new_rate`, ex date), `cash_mergers` for our universe from at least 2020 —
e.g. NVDA 10:1 on 2024-06-10, WMT/VZIO cash merger 2024-12-03.

Why it matters for tests specifically: `historical_data_*` bars are **unadjusted**. Today
that is harmless because the tables start 2026-02-23 and no split has landed inside them.
The moment §2.1/§2.2 backfills reach 2024 or earlier, an unflagged split turns one bar
into a 900% return. A splits table is the cheap guard, and the dividend rows additionally
let the 4w/12w grade be a total return rather than a price return.

### 2.5 Asset metadata — the SHORT leg

`broker_costs.TARIFFS` carries `borrow_annual_bps=None` for `just2trade` and `centras`
with an explicit comment that unverified is not zero, and the module refuses to cost a
SHORT rather than guess. `GET /v2/assets/{sym}` gives, per name: `shortable`,
`easy_to_borrow`, `marginable`, `fractionable`, and `maintenance_margin_requirement`
(measured: AAPL 30, XLK 30, **TSLA 50**).

Unblocks: a feasibility filter for the SHORT half of the `alldir` curves — a book whose
shorts are not actually borrowable is not a book — and a margin-aware sizing check against
the `regt_buying_power` the client already reads.

**PIT caveat:** these flags are *current*, with no history. They may qualify a live or
forward test, and may never gate a historical one — a name easy to borrow today says
nothing about 2026-05. Advisory only.

### 2.6 Market calendar

`intraday_entry.py` hardcodes `SESSION_BARS = 390` and 09:30–16:00. That is right for
every day in the current window, and wrong for half-days (13:00 close, 210 bars): the
grid would run off the end of the session and the eod seam check would fire on data that
is simply absent. No cohort Monday in the 2026-04-27 → 2026-07-27 window is a half-day, so
this is **latent, not live** — but it becomes live the moment §2.1 extends the harness
backwards, where every year contains three of them. `/v2/calendar` supplies exact open,
close, pre/post-session bounds and `settlement_date` per date.

### 2.7 Fills, activities, portfolio history — the forward record

`costs.py` is a flat, frozen bps-per-side model, and `broker_costs.py` shows the spread
between real tariffs (~0.11pp to ~0.43pp per hedged round trip) against a gross edge of
+0.2…+0.9pp per 4 weeks — i.e. the cost assumption is the same order as the effect. Paper
fills give the first *measured* number in that chain: submitted price vs
`filled_avg_price`, per order, per name.

Status check, so nobody plans against something that isn't there yet: **`paper_orders` and
`broker_state` do not exist in the database.** `ledger.ensure_schema` creates them on
first run, and `LT_BROKER_ENABLED` is `false` with `LT_BROKER_DRY_RUN` `true`. This
dataset accrues from the day the layer is switched on and not before — which is an
argument for switching it on in dry run early, so the ledger fills while the decision is
still being made.

`portfolio/history` additionally gives a broker-computed equity curve — an independent
check on the harness's own P&L arithmetic, which is currently self-graded.

### 2.8 Quotes and trades

Historical tick quotes/trades are served for the fill minute. That converts the cost model
from an assumption into a measurement: the actual bid-ask at 12:01 ET, per name, per day,
across the book — instead of the single `--cost-bps` knob the `cost10` curve variant
sweeps. Worth doing once over the 121 curve34 entries before trusting any net-of-cost
verdict.

### 2.9 What is *not* unlocked: options IV history

`options_iv` holds **2026-07-15 → 2026-08-08** (2,745 rows, 120 tickers) — 24 days, far
short of an IV percentile. Alpaca does not close the gap: the chain snapshot returns
`latestQuote` only, with no greeks or IV, and per-contract bars begin at contract listing.
D3 row 1 (CBOE DataShop / ORATS) stands unchanged.

---

## 3. Which test **periods** this actually opens

The question that matters more than "which table": *can the agent now be tested over
windows it could not be tested over before?* Measured answer: **yes, but not the way the
price backfills suggest.** Three different things are being confused by the word "period",
and they have three different answers.

### 3.1 New periods available today, for free, with no backfill at all

**The horizons that matured while nobody graded them.** `backtest_runs` rows are written
per horizon and graded independently, and the +12w leg of nearly every A/B sweep has come
due without being read:

| Sweep | Horizon | Mature & ungraded (traded) | as_of range |
|---|---|---|---|
| `wf_30x_v0.1` | 84d | 120 (51) | 2026-04-24 → 2026-06-19 |
| `wf_30x_f12_v3` | 84d | 120 (43) | same |
| `wf_30x_f12_cons2` / `_cons` | 84d | 120 (42) each | same |
| `wf_30x_v0.2_ct` / `v0.2` | 84d | 120 (40 / 39) | same |
| `monday_t1` / `monday_t1_pit` | 84d | 90 (30 / 14) | 2026-04-27 → 2026-07-13 |
| **`intraday_curve34`** | **28d** | **374 (101)** | 2026-04-27 → 2026-07-27 |
| `sunday_sim` | 14d | 128 (9) | 2026-07-12 → 2026-07-19 |
| `longterm_agent_results` 0.2.0 (live book) | 14d | 130 (12) | 2026-06-19 → 2026-07-26 |
| `shift_library` 0.2.0 + 0.3.0 | 28d | 41 | 2026-07-02 → 2026-07-09 |

~20 sweeps carry a mature, unread 12-week leg. This is a genuinely new measurement period
— the horizon at which a multi-week thesis is supposed to pay — and it costs **zero LLM
tokens**, because the decisions were made months ago. It is the cheapest new evidence
available anywhere in this document.

**Grading cutoffs today (2026-08-09):** 2w → `as_of ≤ 2026-07-26` · 4w → `≤ 2026-07-12` ·
12w → `≤ 2026-05-17`.

**Harness-level periods, 2016 → now, immediately.** Tests of the *machinery* rather than
the agent — fill convention (`mid_1m` vs `open_t1`, strictly-after, `--entry-tolerance`),
the two-leg holding grid, the benchmark, the cost model — need entry timestamps and
prices, not LLM decisions. Alpaca supplies 2,664 SPY sessions from 2016-01-04. So the
convention that currently rests on 14 Mondays can be stress-tested across a decade this
week, using replayed or synthetic entry stamps.

### 3.2 What the price backfills do **not** buy: new agent cohorts

Computed explicitly, because it is the tempting wrong conclusion:

| Window | Weekly cohorts | Regimes |
|---|---|---|
| Today's pre-registered window (2026-04-24 → 2026-07-27) | 14 | bull 10 · flat 4 · **bear 0** |
| **+ free daily/minute backfill, no news** | **14** | unchanged |

Minute and daily bars extend *grading and entry* backwards, not *deciding*. A decision at
`as_of` needs 60 days of news background with ≥8 articles per ticker
(`LOOKBACK_DAYS_BACKGROUND=60`, `MIN_ARTICLES_FOR_BACKGROUND=8`), and the corpus is dense
only from 2026-03. **The corpus is the only gate that binds.** Everything else already
clears, or clears for free.

Regime footnote, since the repo says "one regime": across *all* 32 stored cohort dates the
labels are bull 20 / flat 5 / bear 4 — but the four bear dates are 2026-03-16 → 2026-04-06,
from earlier sweeps. The pre-registered window itself contains **no bear cohort at all**.

### 3.3 What a deeper corpus would open — quantified

Weekly Mondays by regime (SPY trailing-60d, ±3% band — `backtest._regime`'s own rule,
recomputed on Alpaca's adjusted SPY history):

| Corpus reaches back to | Cohorts | bull | flat | bear | vs today |
|---|---|---|---|---|---|
| today | 14 | 10 | 4 | 0 | 1× |
| 2025-01 | 82 | 38 | 32 | **12** | 5.9× |
| 2021-01 | 291 | 149 | 99 | **43** | 21× |
| 2016-07 (max) | 526 | 280 | 175 | **71** | 38× |

And the stress windows that are *only* reachable that way:

| Window | Cohorts | Regime split | VIX %ile / level (start·mid·end) |
|---|---|---|---|
| 2018 Q4 selloff | 15 | bear 11 · flat 3 · bull 1 | 26/12.1 · 84/18.1 · 91/21.4 |
| 2020 COVID crash + rebound | 16 | bear 10 · bull 6 | 47/13.7 · **98/41.7** · 93/27.5 |
| 2022 bear | 44 | bear 23 · flat 15 · bull 6 | 56/17.2 · 80/24.8 · 77/25.8 |
| 2023 regional-bank stress | 9 | flat 7 · bull 2 | 41/18.5 · 42/18.7 · 24/15.8 |
| 2025 (last full pre-corpus year) | 52 | bull 25 · flat 19 · bear 8 | 27/16.1 · 28/16.4 · 10/13.6 |

The 2025 row is the cheapest honest target: one year back, 52 cohorts, the first bear
cohorts the agent would ever have seen, and no survivorship problem worth arguing about.

**Secondary gates that bind once the corpus moves** (each free to clear, but they must be
cleared *before* the cohorts are run, not after):

| Source | Earliest today | Effect before that date |
|---|---|---|
| `stock_prices_daily` + 180d technical | 2024-12-14 | fatal — no technical window → fix with Alpaca daily (2016) |
| `fundamentals` (edgar) | 2019-07-18 | fundamentals node degrades → EDGAR backfill is free |
| `filings` / `insider_trades` | 2025-07-15 | advisory tools return "no data" → EDGAR backfill to 2019 is free |
| graph events (Neo4j) | 2025-10-17 | analog/memory bridge degrades |
| `options_iv` | 2026-07-15 | advisory only; not fixable (see §2.9) |
| minute bars (`mid_1m` entries) | 2026-02-23 | no intraday entry → fix with Alpaca SIP (2016) |

So a 2021→ or 2016→ run is not one decision but four: corpus licensing, an EDGAR
re-backfill, accepting degraded graph/options advisories for the older era, and the LLM
bill for 291–526 cohorts × universe.

---

## 4. How deep is the corpus really — and how much of it is already spent

§3 treated the corpus as a date range. It is not: it is a **(ticker × as_of) rectangle**,
and both edges are shorter than the news table suggests. This section measures the
rectangle, then overlays the sweeps that have already consumed it.

### 4.1 Measuring it: use the agent's own verdict, not an article count

The obvious proxy — count articles matching the company regex in the trailing 60 days
against `MIN_ARTICLES_FOR_BACKGROUND=8` — is wrong, and worth recording as wrong so nobody
rebuilds it. `news_corpus_tool` retrieves along **two** paths, graph AFFECTS and regex
recall, and the graph path carries most of the volume. Calibrated against the stored
`article_count` of 25 live runs: regex-only mean **74.7** vs actual mean **302.8** — a ~4×
undercount. It misclassifies a name as starved when it is not (AMAT: 0 by regex, 58
actually).

The honest measure is already stored: **`direction='NO_DATA'` is the agent stating that the
corpus was insufficient for that cell.** Every swept cell carries one.

### 4.2 The real floor is the graph, not the news table

| Month | news rows | graph events | events / article |
|---|---|---|---|
| 2026-03 | 6,549 | 253 | **0.039** |
| 2026-04 | 8,061 | 4,112 | 0.510 |
| 2026-05 | 7,810 | 3,498 | 0.448 |
| 2026-06 | 7,162 | 1,900 | 0.265 |
| 2026-07 | 6,687 | 1,691 | 0.253 |
| 2026-08 | 1,850 | 368 | 0.199 |

News is dense from 2026-03, but the AFFECTS path — the precision half of the corpus and
roughly 4× its volume — only switches on in **2026-04**. A March `as_of` runs on a corpus
structurally different from an April one, at ~8% of the event density.

**So the corpus is complete from 2026-04, not 2026-02**, and the March cohorts
(2026-03-16 → 2026-04-06) are a different data regime rather than simply an earlier one.

### 4.3 What the sweeps have already consumed

Grid actually swept: **32 dates, 2026-03-16 → 2026-07-27, 120-name universe = 3,840 cells**,
of which **1,956 (50.9%) have been run at least once**. Burn depth:

| Sweeps on the cell | Cells |
|---|---|
| 1 | 904 |
| 2 | 601 |
| 3 | 174 |
| 4 | 7 |
| **13–16** | **270** |

The tail is the standard grid the ledger's attempt counter already tracks — 270 cells have
hosted 13 to 16 different `backtest_id`s. Under the burned-grid rule those cells can only
ever REJECT.

### 4.4 The unburned half is mostly a mirage

1,884 cells (49.1%) have never been swept, which sounds like ample untouched grid. It is
not:

* **1,536 of them (82%) sit on the 48 universe names that have never been run anywhere** —
  not in a sweep, not in the live book. There is *zero* evidence the corpus supports them:
  ABT, ADP, ADSK, AEP, AMGN, AMT, APD, BLK, BMY, CB, CCI, CDNS, CHTR, CI, CMG, CVS, DE,
  DHR, DUK, ECL, EL, EOG, EQIX, FTNT, GILD, HUM, ISRG, LIN, LRCX, MDT, MMM, MPC, NEM, NOC,
  PLD, PSA, PSX, REGN, SHW, SLB, SNPS, SO, SPGI, TGT, TJX, UNP, VRTX, XYZ.
* They are utilities, REITs, industrials and defensive healthcare — exactly the profile of
  the swept names that *do* starve. Measured NO_DATA rates among names that were run:
  **TMO 100%** (100/100 — never once had a corpus), INTU 93.6%, ACN 90.0%, TXN 60.6%,
  PG 58.0%, ABBV 41.6%, CSCO 38.7%, JNJ 37.9%, TMUS 36.3%.
* 71 of the 72 swept tickers produced at least one real decision; TMO produced none.

Netting it out: the unburned pool on names with a **proven** corpus is roughly **348 cells**,
not 1,884. The rest is unexplored precisely because the corpus cannot feed it — which is
why `intraday_curve34` had to be selected by *burn intensity* among proven names rather
than by a fresh/burnt flag.

### 4.5 Per-date verdict

| as_of range | Cells swept | Burn depth | Usable for a new test? |
|---|---|---|---|
| 2026-03-16 → 2026-04-06 | 72/date | 13–16 sweeps on ~30 cells/date | **No** — burned, *and* pre-graph corpus regime |
| 2026-04-13 → 2026-04-20 | 72/date | heavy | **No** — burned (the March holdout family ran every live name) |
| 2026-04-27 → 2026-07-20 | 64/date | 1–4 sweeps | **Reject-only** — the pre-registered grid |
| 2026-07-27 | 34 | 1 (`intraday_curve34`) | Reject-only for 34 names; ~14 proven names still free |
| **2026-08-03 →** | **0** | **0** | **Yes — the only clean acceptance window** |

### 4.6 The capacity that is left is shrinking

The forward window is the only place acceptance is permitted, and it is getting thinner.
On a **fixed** 34-name set (so this is not a universe artefact), the agent's own NO_DATA
rate by cohort:

| Cohort | NO_DATA |
|---|---|
| 2026-05-15 … 05-29 | 0 – 6.1% |
| 2026-06-01 … 06-19 | 4.8 – 7.1% |
| 2026-06-22 … 07-06 | 8.8 – 11.8% |
| **2026-07-12 … 07-27** | **14.7 – 17.6%** |

Same direction and same timing as the events/article collapse in §4.2 (0.51 → 0.20). Each
forward cohort is worth roughly **10% fewer usable cells than it was two months ago**, and
at ~48 usable cells per weekly cohort, the pre-registered `confirms_only` power requirement
of 15–20 fresh cohorts is a 4–5 month wait that is currently getting longer, not shorter.
**Fixing the event-extraction rate buys more test capacity than any backfill in this
document** — it is the only lever that acts on the unburned side of the grid.

---

## 5. Data already in Postgres that LT tests do not use

| Data | State (measured 2026-08-09) | Usable as a test input? |
|---|---|---|
| `macro_series` | 9 series, deep (VIX from 1990, 9,218 rows), current | **Yes, and cheap.** `regime` is NULL on 6,988 of 20,472 `backtest_runs` rows and on **all 476** `intraday_curve34` rows. Stratifying existing sweeps by VIX/credit regime costs one backfill query and needs no new decisions. |
| `filings` (1,811) + `insider_trades` (20,131), 2025-07-15 → | wired only as advisory LLM tools (`filings_tool.py`) | Yes as *evidence-ablation*: re-run a sweep with the tools off/on and measure the delta. Never yet done. |
| `longterm_run_snapshots` | 258 rows, 4 pipeline versions, 2026-06-19 → 2026-08-09 | The declared training set for the tuning loop, never used as one. Enough rows now for a first per-node hindsight read. |
| `agent_results` | 12,939 rows, **stalled 2026-06-05** | Event→reaction only, per the reaction-news census verdict — never as a signal. Restarting the pipeline is a Data Expansion B1 item that is losing data *now*. |
| `news.embedding` | June 2026 gap persists: **4,909 / 7,162 embedded (69%)** | Retrieval silently skips a month of a 14-cohort window. Re-embed before any retrieval-sensitive test. |
| `options_iv` | 24 days | Not yet. See §2.9. |
| `simulated_trades` | 19 rows, one run 2026-06-06 | Dead table; the holding-curve work writes CSVs instead. Either adopt it or drop it. |
| `longterm_agent_results` 0.2.0 | 258 rows, 25 traded, **0 graded**, `fill_convention` NULL on all | The live forward book is ungraded — `worst_position_return_4w` is filled on 5 rows of 277, `aux_targets` on 3, `fwd_return_12w` on 0. Grading it is a prerequisite for any forward claim, and needs no new data at all. |

---

## 6. Suggested order

0. **Grade what has already matured** (§3.1) — the +12w leg across ~20 sweeps, curve34's
   +4w, the live book's +2w, 41 `shift_library` rows. No new data, no LLM cost, and it is
   the only item here that produces *evidence* rather than *capability*. Acceptance: every
   sweep with a matured horizon has `outcome_filled_at` set, and the 12w read is written up
   next to the 2w/4w ones in [BACKTEST_LEDGER.md](BACKTEST_LEDGER.md).
1. ~~**Minute-bar backfill for the ETFs**~~ — **DONE 2026-08-09**, independently of this
   document: SPY + 11 sector tables, 2026-04-01 → now, 92/92 complete, validated against
   Alpaca SIP to 0.000 bps. What remains is the single-name side: 28 of the 64 live
   tickers carry at least one short session in the last 17 (worst COST 3/17 complete,
   ABNB 6/17), and the pre-2026-04 window for any name is still unrepairable from Yahoo.
2. **Re-run the pre-registered curve34 reading** on repaired bars; publish the diff against
   the current curve. Acceptance: the delta is reported, whatever its sign.
3. **Splits/dividends table + calendar table** (small, and both are guards the backfills
   below depend on).
4. **Daily bars to 2016**, then **minute bars to 2016** for the backtest universe only.
   Acceptance: beta and the 180d window compute at an archival `as_of`.
5. **Asset flags** as a daily snapshot (forward PIT archive, like the yfinance
   fundamentals snapshots) → SHORT feasibility, `borrow_annual_bps` resolved.
6. **Broker layer on in dry run**, so `paper_orders` starts accumulating fills.
7. **News archive** — the only item that adds new *agent* periods (§3.3). Only after the
   ToS answer, only as a tagged era with a new `DETECTION_VERSION`, and 2025-01 first:
   52 cohorts and the first bear cohorts, before paying for a decade.

Item 0 is free evidence sitting unread. Items 1–3 are pure repair — they change what the
*existing* pre-registered tests are measuring, so they come before anything that adds new
questions.

---

## 7. Rules that still bind

* **PIT.** A backfilled bar or article is only usable at an `as_of` if it was knowable
  then. Historical bars are; *current* asset flags and *current* option chains are not.
* **Detection version.** Corpus changes ship as a new `DETECTION_VERSION` +
  `BUCKET_CACHE_VERSION`; results across versions are not commensurable.
* **Grading separation.** None of this may write to `longterm_agent_results` or
  `backtest_runs` outcome columns outside the grading job.
* **Licensing.** Every new source carries an explicit "may we store it?" note before the
  first row lands.
* **Entitlements are measured, not assumed.** Historical SIP answered today; real-time SIP
  answered 403 today. Both can change — re-probe rather than inherit this document's
  claims.

## Appendix — how these figures were produced

Every number here came from read-only probes against the live Alpaca API and the live
`trade_agent` database on 2026-08-09 — no writes, no orders, no state touched. The probes
were throwaway scripts and are **not checked in**; the queries that matter are short enough
to reproduce directly:

```sql
-- session completeness for one ticker (390 = whole regular session)
SELECT timestamp::date d, count(*) FROM historical_data_aapl
 WHERE timestamp::time >= '09:30' AND timestamp::time < '16:00'
 GROUP BY 1 HAVING count(*) < 390 ORDER BY 1;

-- which traded curve34 entries sit on a damaged session: same count per (ticker, as_of)
SELECT DISTINCT ON (ticker, as_of) ticker, as_of FROM backtest_runs
 WHERE backtest_id='intraday_curve34' AND traded ORDER BY ticker, as_of;
```

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY_ID" -H "APCA-API-SECRET-KEY: $ALPACA_API_KEY" \
  "https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&timeframe=1Min&feed=sip&start=2026-04-27T13:30:00Z&end=2026-04-27T20:00:00Z&limit=10000"
```

Swap `feed=sip` for a timestamp inside the last 15 minutes to re-check the real-time
entitlement (expected: 403 with `subscription does not permit querying recent SIP data`).
