# Long-Term Trading Agent — Build Plan

A second agent alongside the existing `news_agent` (TradingAgent). Instead of reacting to a single news item with an intraday signal, it analyzes the **whole news flow of one company over a period**, builds the company's **news background** (baseline narrative, sentiment, attention), detects **shifts** in that background, validates the picture against **company fundamentals**, and outputs a **multi-week / multi-month** trade signal.

---

## 1. Positioning vs the existing agent

| | `news_agent` (existing) | `longterm_agent` (new) |
|---|---|---|
| Trigger | One news article arrives | Ticker + analysis date (scheduled, e.g. weekly) |
| Input | `news_text`, `news_id`, `timestamp` | `ticker`, `as_of`, `lookback_days` |
| Context window | Last 24h news + 10 similar events | Full company news corpus over 60–90 days |
| Price data | 1-minute bars, 40-min impact windows | Daily bars, 6-month technical context |
| Core question | "Will this headline move the stock in the next hour?" | "Has the narrative around this company changed, and do fundamentals support a position for weeks?" |
| Risk gate | Pre-news leak, volume spike | Priced-in move over weeks, earnings event risk, data sufficiency |
| Output | Intraday signals with SL/TP | Thesis + LONG/SHORT/NEUTRAL, conviction, horizon, invalidation conditions |

Framework: **LangGraph** (same as `news_agent`) — the workflow is a multi-step pipeline with a reflection loop, custom state, and parallel fan-out; no need for Deep Agents. All LangChain tools and the Pydantic structured-output pattern carry over.

---

## 1.1 System schema — current state (snapshot @ 2026-06-16)

Status legend:  ✅ built & verified   ·   🟡 built, smoke-tested only   ·   ⏳ planned / deferred

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ DATA LAYER  (fetchers write · agent reads · all as-of / point-in-time)        │
│   news 26.8k ✅   stock_prices_daily (2y, 120 tk + SPY) ✅                     │
│   fundamentals: EDGAR PIT statements + yfinance snapshots ✅                   │
│   Neo4j graph: Article→Event→Story, AFFECTS sentiment ✅                       │
│   shift_library ✅   bucket_summary_cache ✅   longterm_agent_results ✅        │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                 │ read (filed_date/fetch_date/published ≤ as_of)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ AGENT PIPELINE  (LangGraph · input: ticker + as_of)                           │
│                                                                               │
│   corpus_collection ✅ ──(articles < 8)──► NO_DATA → END                       │
│        │  (Neo4j AFFECTS ∪ word-boundary regex, weekly buckets)               │
│        ▼                                                                       │
│   background_profiling ✅  map→reduce, per-(ticker,week) cache, retry+fallback │
│        ▼                                                                       │
│   shift_detection ✅  metrics → DETERMINISTIC shift_score / has_shifted        │
│        ▼                                                                       │
│   fundamentals_analysis ✅  trend + valuation + expectation gap → verdict      │
│        ▼                                                                       │
│   similar_shift_analysis ✅  shift_library analogs (leakage+version filtered)  │
│        ▼                                                                       │
│   ┌── deep_analysis ⏳ (7A) ── bounded ReAct tool loop (advisory only) ──┐     │
│   └──────────────────────────────────────────────────────────────────────┘   │
│        ▼                                                                       │
│   synthesis ✅  news×fundamentals matrix → LongTermSignal (+retry)            │
│        ▼  ▲ reflection loop                                                    │
│   reflection ✅ ──(needs_refinement)──► shift_detection / fundamentals        │
│        ▼                                                                       │
│   risk_gate ✅  priced-in · earnings · DATA-SUFFICIENCY guard → filtered_signal│
│        ▼                                                                       │
│       END → persist (longterm_agent_results + shift_library)                  │
└─────────────────────────────────────────────────────────────────────────────┘

EVAL & OBSERVABILITY
  pytest suite (35) ✅   LangSmith tracing ✅   detection backtest 🟡   full-signal backtest 🟡
  shift_library backfill (8-ticker smoke) 🟡   full-universe replay ⏳   calibration ⏳

ADVANCED REASONING (see §10)
  7A deep_analysis — LangChain tools + bounded ReAct ⏳   ·   7B Deep Agents harness ⏳
```

Backtest results so far are **n=17 single-regime noise** — the harness + agent behavior are validated, the *strategy* is not. Real validation = forward accumulation across regimes.

---

## 2. What gets reused from this project

- `news_agent/database/db_manager.py` — Postgres + pgvector access, `validate_timestamp` time-leakage discipline
- `graph_db/neo4j_manager.py` + `news_agent/tools/graph_tools.py` — Stories/Events/`AFFECTS` sentiment, `get_story_momentum`, `get_novelty_score`, `get_events_for_ticker`
- `model_config.py` — single point of LLM/embedding configuration (OpenRouter), `news_agent/llm_errors.py` retry handling
- `news_agent/schemas.py` conventions — Pydantic structured outputs per node
- `news_agent/tools/technical_analysis_tool.py` (with longer lookback) and `sector_competitor_tool.py`
- `batch_analysis/` pattern + `agent_results` table design for persistence
- `profit_compute/` simulators (`trade_simulator_yahoo.py` is the closest starting point for daily-bar backtests) and `calibrate_confidence.py`
- LangSmith tracing setup (`run_agent_with_langsmith.py`), Docker/compose deployment pattern, `telegram_bot/` for notifications

---

## 3. Phase 0 — Data foundations (prerequisite work)

### 3.1 Company → news corpus retrieval — ✅ BUILT ([news_corpus_tool.py](longterm_agent/tools/news_corpus_tool.py))
Today ticker filtering is `ILIKE '%TICKER%'` on title/summary/full_text ([db_manager.py:366](news_agent/database/db_manager.py:366)) — too weak to build a corpus. `get_company_news_corpus(ticker, as_of, lookback_days, …)` merges two sources, dedupes by `news.id`, and buckets by week:
1. **Neo4j (precision + sentiment):** `(Company {ticker})<-[AFFECTS]-(Event)-[SOURCED_FROM]->(Article)`, filtered to company-domain event types + `confidence ≥ 0.5`, keeping the `AFFECTS.sentiment` per article (free per-article sentiment labels).
2. **Word-boundary regex recall** (`~* '\y(name|TICKER)\y'`) on title/summary — replaces naive ILIKE.

**Two bugs found & fixed against live data (the reason to build, not just plan):**
- **Substring ILIKE was catastrophic** — `'%CAT%'` matched "category"/"indicate" → 721 false positives for CAT. Fixed with Postgres word-boundary regex (`~*` + `\y`) and dropping bare tickers < 3 chars. CAT → 6.
- **Event-date vs publication-date** — the Neo4j path originally filtered on `e.date` (the event's *real-world* date: a 2024 earnings event, a 2017 conflict), which silently dropped any company whose events are dated outside the window. CAT had **23 in-window articles → 0**. Fixed by removing the `e.date` filter and windowing on `news.published` downstream. **Result: CAT 6 → 27 (clears the gate); NVDA graph 39 → 1020.** The graph path is far stronger than text matching (CAT: 23 graph vs 6 regex; the graph resolves company linkage even when the article never says "Caterpillar").

**Measured coverage (2026-06-12, after fixes):** NVDA 1106, AAPL 742, JPM 762, TSLA 557, BA 88, GE 32, CAT 27, SBUX 27 — far more tickers now clear `MIN_ARTICLES_FOR_BACKGROUND=8` than the text-only baseline (which gave Apple 357, Caterpillar 3). The graph's `AFFECTS` is the workhorse; the company-domain + confidence filter tames the over-broad macro fan-out (raw `AFFECTS` links top tickers to ~32% of the whole corpus).

**Measured coverage (2026-06-12):** Neo4j holds 19,094 Articles, 9,447 Events, 4,780 Stories, 2,247 Companies, 17,892 `AFFECTS` rels — but linkage is **over-broad**: top tickers (NVDA, MSFT, GOOGL, SPY…) are each linked to ~6,000 articles (≈32% of the corpus), because macro events fan out `AFFECTS` to many tickers. The Neo4j path therefore needs a **precision filter**: require `AFFECTS.confidence` above a threshold, restrict to company-domain event types (`corporate_announcement`, `earnings`, `product_launch`, `merger_acquisition`, `leadership_change`, `legal`, `regulatory`), and/or require the company to be mentioned in the article text. ILIKE by company name gives the precision baseline (e.g. last 90d: Apple 357, Nvidia 363, Tesla 134, Microsoft 142, Starbucks 36, Caterpillar 3).

### 3.2 Daily price data
**Reality check:** the `stock_prices` table is empty — actual prices live in 120 per-ticker tables `historical_data_<ticker>` (1-minute bars, **2026-02-23 → today**, ~30k rows/ticker). That is ~3.7 months — enough to aggregate into ~75 daily bars, but not enough for 180d technicals (50/200 MA). Add `stock_prices_daily` (ticker, date, OHLCV) backfilled via yfinance (`period="2y", interval="1d"`) and appended daily by the existing fetcher container. Used for: long-term technicals, priced-in check, and backtesting.

### 3.3 Fundamentals store — ✅ BUILT (yfinance + SEC EDGAR, free, point-in-time)
**Decision: fundamentals are ingested by the data-ingestion module, never fetched live by the agent** ("fetchers write, agents read", same as news/prices). The v1 stack is **100% free** — SEC EDGAR for true point-in-time historical statements + yfinance for the data filings don't contain (valuation ratios, analyst estimates, earnings dates). No paid provider needed for v1; FMP Premium / Sharadar remain the documented upgrade path (§11) if a deep historical backtest later demands clean restated-aware PIT or historical analyst consensus.

**Implemented files (in `bloomberg_news_fetcher/`):**
- `edgar_fetcher.py` — resolves ticker→CIK (cached `sec_cik_map.json`), pulls the XBRL `companyfacts` feed, and normalizes us-gaap concepts into per-period **income / balance / cash-flow** statements. Each statement carries its **original filing date** (`filed_date`) = the true point-in-time key. Keeps the as-*originally*-reported value (earliest filing) so backtests never see later restatements; balance-sheet comparatives are de-duplicated by balance date. Curated concept maps cover revenue, margins, EPS, assets/liabilities/equity, cash, debt, operating CF, capex, dividends, buybacks. Honours SEC fair-access (`SEC_EDGAR_USER_AGENT`, ≤10 req/s).
- `fundamentals_fetcher.py` — orchestrator. Writes **EDGAR** statements + weekly **yfinance** snapshots (`info`, `earnings_dates`, `analyst_estimates` incl. `eps_trend`, `eps_revisions`) to the `fundamentals` table. CLI: `--tickers`, `--edgar-only`, `--yf-only`, `--stats`.
- `create_fundamentals_table.sql` — schema below.
- Wired into `auto_news_fetcher.py` as a **weekly** job (off-market, `FUNDAMENTALS_FETCH_WEEKDAY`/`_TIME`), gated by env flag `FETCH_FUNDAMENTALS=true` (default off).

**Verified (AAPL, 2026-06-15):** 70 EDGAR statement rows spanning **2019→2026** (7 yrs; 28 income, 28 balance, 14 cash-flow) + yfinance snapshots. PIT query confirmed: filtering `filed_date <= 2025-06-01` returns only statements known by then — no lookahead.

**Two known v1 limitations (acceptable, documented):** (1) mid-year *quarterly* cash flow is omitted — 10-Q cash flows are YTD, so only Q1 + FY pass the single-period filter (annual FCF, the valuation-relevant figure, is clean); deriving Q2/Q3 via YTD subtraction is a read-side enhancement. (2) Historical *analyst estimates* are not point-in-time (yfinance gives only the current snapshot) — the weekly snapshot builds a forward PIT archive instead. Both already noted in §8.

```sql
CREATE TABLE fundamentals (
    id            SERIAL PRIMARY KEY,
    ticker        VARCHAR(10) NOT NULL,
    source        VARCHAR(20) NOT NULL,   -- 'edgar' | 'yfinance'
    kind          VARCHAR(40) NOT NULL,   -- edgar: income_statement|balance_sheet|cash_flow
                                          -- yfinance: info|earnings_dates|analyst_estimates|eps_revisions
    period_end    DATE,                   -- statement period end (NULL for snapshots)
    fiscal_period VARCHAR(10),            -- 'Q1'..'Q4' | 'FY'
    filed_date    DATE,                   -- EDGAR original filing date = point-in-time key
    fetch_date    DATE NOT NULL,          -- snapshot key for yfinance rows
    dedup_key     TEXT NOT NULL UNIQUE,   -- version-independent ON CONFLICT target
    data          JSONB NOT NULL,
    created_at    TIMESTAMP DEFAULT NOW()
);
```

**Agent read pattern (as-of, no lookahead):** EDGAR rows → `WHERE filed_date <= :as_of`; yfinance rows → `WHERE fetch_date <= :as_of`. The fetcher stays **dumb** (stores normalized values only); derived metrics (growth %, margin trends, deltas) are computed in the agent's read-side `fundamentals_tool` so definitions can evolve without re-backfilling.

### 3.4 Results table
`longterm_agent_results`, mirroring `agent_results`: ticker, `as_of`, agent_version, `background_profile` JSONB, `background_shift` JSONB, `fundamentals_summary` JSONB, `signal` JSONB, execution_time.

### 3.5 News history depth — measured
Actual `news` table contents (queried 2026-06-12): **26,833 articles**; sparse before Feb 2026 (a handful of rows), 879 in Feb, then dense from March 2026 onward (~6.5–8k/month, ≈250/day; 22,861 articles in the last 90 days). Consequences are worked out in §8 (Data sufficiency & testing): dense history starts **2026-03-01**, which bounds both the usable lookback window today and how far back the backtest can go.

---

## 4. Agent architecture (LangGraph)

Package layout mirrors `news_agent`:

```
longterm_agent/
├── agent/
│   ├── state.py            # LongTermAgentState
│   ├── nodes.py
│   └── graph.py
├── tools/
│   ├── news_corpus_tool.py        # §3.1 retrieval
│   ├── background_metrics.py      # quantitative sentiment/attention time series
│   └── fundamentals_tool.py       # §3.3 reader + trend computation
├── schemas.py              # Pydantic outputs
├── config.py               # LongTermAgentConfig
├── longterm_agent.py       # LongTermAgent.run(ticker, as_of)
├── create_shift_library_table.sql   # ✅ written — shift library w/ agent_version
├── backfill_shift_library.py        # replay past as_of → detect+record; doubles as backtest
└── tests/
```

### Graph flow

```
corpus_collection ─► background_profiling ─► shift_detection ─► fundamentals_analysis ─► similar_shift_analysis ─► synthesis ─► reflection ─► risk_gate ─► END
      │               (Send fan-out over           ▲                    ▲                  (analog base rates)                    │
      │ <8 articles    weekly buckets)             └──── reflection loop ┴───────────────────────────────────────────────────────┘
      ▼
  NO_DATA early exit
```

### Nodes

**1. `corpus_collection`** (no LLM)
Fetch corpus via §3.1 for `[as_of − lookback_days, as_of]`, bucket by week (13 buckets for 90d), compute per-bucket article counts. If total < `MIN_ARTICLES_FOR_BACKGROUND` (≈8) → early exit with a NO_DATA verdict (same pattern as the untraced significance pre-check in `TradingAgent.run()`).

**2. `background_profiling`** (LLM, map-reduce)
- **Map:** one cheap-model call per weekly bucket (LangGraph `Send` fan-out) → `PeriodBucketSummary`: themes, key events, sentiment −1..1, attention level. Seed each call with the `AFFECTS` sentiments from Neo4j where available.
- **Reduce:** main model merges buckets into `NewsBackgroundProfile`: dominant narratives (linked to Neo4j Stories), sentiment timeline, attention timeline, recurring risks and catalysts. This *is* the "news background".

**3. `shift_detection`** (quantitative + LLM)
- Quantitative series from `background_metrics.py`: bucket sentiment means, article counts, story momentum (`get_story_momentum`), novelty (`get_novelty_score`).
- Metrics: sentiment delta of recent window (default last 14d) vs baseline (prior 76d) with z-score; attention spike ratio; new-story emergence (Stories whose `start_date` falls in the recent window); narrative turnover (theme overlap recent vs baseline).
- LLM interprets: what changed, inflection date, direction and magnitude, candidate catalysts → `BackgroundShift`.

**4. `fundamentals_analysis`** (LLM + tools)
- `fundamentals_tool` (DB reader over the `fundamentals` table, as-of filtered): trend over last 4–8 quarters (revenue growth, margins, FCF, debt), valuation vs sector (reuse `sector_competitor_tool`), next earnings date.
- `technical_analysis_tool` on daily data, 180d lookback: trend, 50/200 MA, long-term support/resistance.
- **Materiality weighting:** revenue mix from the statements tells the profiler which news themes matter — for a company with 60% of revenue in one segment, news about that segment weighs more in the sentiment timeline.
- **Expectation gap:** compare the news shift against analyst estimate revisions — background turned negative but estimates haven't moved → potentially early (the alpha case); estimates already cut → the shift is likely priced in (feeds the risk-gate priced-in check).
- LLM verdict: does the fundamentals trajectory **confirm, ignore, or contradict** the news-background shift?

**4b. `similar_shift_analysis`** (analogical / base-rate reasoning — placed *after* fundamentals so the query keys on the full fingerprint)
- Builds the current shift's fingerprint (direction, `magnitude_z`, causal event types, novelty, **+ fundamentals posture from node 4**) and embeds it.
- Searches `shift_library` (see below) for the *k* nearest past shifts **whose outcome window completed before `as_of`** (leakage rule) → returns their realized forward-return distribution as a base rate, plus the fundamentals-matched subset. Filters to the running `agent_version` so fingerprints are comparable.
- Cold start (empty library): falls back to on-the-fly analogs from the Neo4j graph (past clusters of similar event types + their daily price reaction). A shift is *computed* from raw data, so detection never depends on the library being populated — see §4.1.
- Output: `ShiftAnalogs` (base-rate stats + nearest cases) feeding synthesis conviction.

**5. `synthesis`** (LLM)
Combine profile + shift + fundamentals **+ analog base rates** into `LongTermSignal`. The core logic is an explicit news×fundamentals matrix in the prompt:

| | Fundamentals improving | Fundamentals deteriorating |
|---|---|---|
| **News shift positive** | High-conviction LONG | Hype risk — low conviction, avoid |
| **News shift negative** | Overreaction candidate — contrarian LONG or NEUTRAL | High-conviction SHORT |

Diagonal cells (confirmation) raise conviction; off-diagonal cells are where fundamentals *prevent bad trades*. Valuation acts as a conviction multiplier (a positive shift at 40× sales ≠ at 12× earnings) and shapes invalidation conditions. Output: direction LONG/SHORT/NEUTRAL, conviction 0–1, horizon (e.g. "4–12 weeks"), thesis summary, supporting factors, key risks, and explicit **invalidation conditions** (what observation kills the thesis).

**6. `reflection`** (LLM) — same pattern as existing agent: quality check, may loop to `shift_detection` or `fundamentals_analysis`, `MAX_REFLECTION_ITERATIONS = 1`.

**7. `risk_gate`** (no LLM) — horizon-appropriate replacements for the intraday checks:
- **Priced-in check:** price change over the shift window vs its typical move; if the stock already moved > k× in the signal's direction, cut conviction / flag (long-horizon analog of `PRE_NEWS_PLAYED_OUT_K`).
- **Earnings event risk:** earnings inside the next N days → flag.
- **Data-sufficiency gate (built):** if there are no *usable income* fundamentals — both the empty case and the banks/financials case where balance-sheet rows exist but revenue/margins are None (filed under unmapped us-gaap tags) — hard-cap conviction below the trade threshold so the signal degrades to FILTERED rather than issuing a confident news-only LONG/SHORT. Verified: NVDA/KO pass (usable), JPM filtered (revenue & margins None).
- Conviction threshold gate → final `filtered_signal`.

### 4.1 Shift library — analogical reasoning & cold start

The `similar_shift_analysis` node reads a **shift library**: a table of every shift the agent has ever detected, with its fingerprint and (backfilled later) its realized forward return. Table defined in [longterm_agent/create_shift_library_table.sql](longterm_agent/create_shift_library_table.sql).

- **A shift is computed, not looked up.** Detection runs the statistical/narrative break metrics over raw news/graph/price data, so an **empty library is fine at cold start** — the library only *caches* detections and remembers their outcomes; it is never a prerequisite for finding shifts.
- **The agent populates it itself by replaying the past.** `backfill_shift_library.py` walks `as_of` backward week-by-week (from 2026-03-01 + window to today) across the universe, runs detection at each point (using only data ≤ `as_of` — no leakage), and records every detected shift. The detector generates its own labels (unsupervised), so no manual seeding. This job **doubles as the Phase 5 backtest** (one pass = library + evaluation).
- **Outcomes are graded later.** A separate grading pass fills `fwd_return_2w/4w/12w` (and the SPY benchmark for excess return) once each shift's horizon matures (≥ that many weeks after its `as_of`). Un-graded rows are ignored by analog search.
- **`agent_version` is load-bearing.** Rows are products of a specific detector. The unique key is `(agent_version, ticker, as_of)`: re-running a *new* version adds rows (old ones kept for audit/comparison) while re-running the *same* version is idempotent. Similarity search filters to the active version so fingerprints are comparable. Mirrors the `agent_name`/`agent_version` convention in `agent_results`.
- **Read/write timing:** READ analogs in `similar_shift_analysis` (during the run, before synthesis); WRITE the fingerprint at persistence (end of run); WRITE outcomes in the deferred grading pass (days/weeks later). The same archive powers `calibrate_confidence.py`, so analogical reasoning and conviction calibration mature together.

### State schema (sketch)

```python
class LongTermAgentState(TypedDict, total=False):
    ticker: str
    company_name: str
    as_of: datetime
    lookback_days: int            # default 90
    shift_window_days: int        # default 14
    messages: List[Dict[str, Any]]
    news_corpus: List[Dict]       # id, published, title, summary, affects_sentiment?
    period_buckets: List[Dict]    # week → article ids + counts
    bucket_summaries: List[Dict]  # map-step outputs
    background_profile: Dict      # reduce output
    background_metrics: Dict      # quantitative series + shift stats
    background_shift: Dict
    fundamentals: Dict
    technical_longterm: Dict
    signal: Dict
    reflection_count: int
    needs_refinement: bool
    loop_to: str
    risk_flags: List[str]
    filtered_signal: Optional[Dict]
```

### Config (new `LongTermAgentConfig`, reusing `model_config.py`)

```python
LOOKBACK_DAYS_BACKGROUND = 60   # start at 60 (dense news only since 2026-03); raise to 90 as history accrues
SHIFT_WINDOW_DAYS = 14
BUCKET_DAYS = 7
MIN_ARTICLES_FOR_BACKGROUND = 8
SENTIMENT_SHIFT_Z_THRESHOLD = 1.0
LOOKBACK_DAYS_TECHNICAL_LT = 180
EARNINGS_RISK_DAYS = 10
LONGTERM_CONFIDENCE_THRESHOLD = 0.55
MAX_REFLECTION_ITERATIONS = 1
MAP_MODEL = GRAPH_LLM_MODEL   # cheap model for bucket summaries
```

---

## 4.2 Reasoning model — how the chain is built and grounded

The nodes in §4 are *stages*; this section is the cross-cutting principle that turns them into a reasoning chain rather than text generation.

### The reasoning unit: evidence → claim (every step grounded)
The load-bearing rule: **no step asserts anything it can't point to.** Each link is `evidence → claim`, where evidence is something *retrieved* — an article quote, a graph edge's `evidence_text`, a sentiment z-score, a filed statement number — never invented. This is the same discipline the existing `news_agent` enforces via `evidence_text` on every graph relationship, and it is what stops the LLM from narrating a story the data doesn't support. Quantitative anchors come **before** LLM interpretation at every quantitative node (shift detection computes the z-score first, *then* the model explains it).

### Three mechanisms that build the chain
1. **Sequential accumulation in message state.** Each node appends its reasoning to a shared `messages` list (LangChain native format, like `news_agent`); every later node sees the full chain so far, so synthesis can reference "the −2.1σ shift from node 3, caused by the export chain, now tested against the financials."
2. **Graph traversal for causal chains (the literal "chain").** `get_causal_chain_with_prices` ([graph_tools.py:200](news_agent/tools/graph_tools.py:200)) traverses `Event —CAUSES→ Event —AFFECTS→ Company` edges, each hop returning its `evidence_text` quote. The reasoning chain is an actual graph path with a verbatim justification per arrow.
3. **ReAct tool loop inside a node.** Like the existing `main_analysis` node, a reasoning node can pause mid-thought, call a tool to fill a gap it noticed (pull a specific quarter, check a competitor, search similar past shifts), then continue. The chain branches to gather evidence and rejoins. The LLM-driven tools are the graph tools, `fundamentals_tool`, and `find_similar_shifts`; the metric/corpus tools run deterministically.

### Worked chain (NVDA)
> Node 2 (profile): "sentiment +0.3 (Mar–Apr) → −0.4 (last 14d); attention 2.1×."
> Node 3 (shift): "quant first — −2.1σ break, inflection ≈ May 28, novelty 0.7 (genuinely new)."
> Node 3 (causal): "graph path — *new export controls* —CAUSES→ *China guidance cut* —AFFECTS(neg)→ NVDA; evidence: '…company expects a $X reduction…'."
> Node 4 (fundamentals): "but EDGAR shows data-center revenue +60% YoY, margin steady → off-diagonal: narrative negative, business improving → do not short; overreaction candidate."
> Node 4b (analogs): "6 nearest past shifts: median −3% / 4w (4 of 6 negative); the 2 fundamentals-matched ones rebounded +5%."
> Node 5 (synthesis): "NEUTRAL-to-LONG, 6–10 wk, conviction 0.45. Invalidation: a second guidance cut, or margin < 70%."
> Node 6 (reflection): "is it already priced in? → priced-in check; if NVDA already fell 15% in-window conviction holds, else loop to node 3."

### Self-correction
The reflection node critiques its *own* chain and may loop back to `shift_detection` or `fundamentals_analysis` (reusing the existing `should_reflect` pattern). In the optional Deep Agents layer (§10) this strengthens into a **red-team subagent** that actively attacks the thesis before synthesis, plus `write_todos` planning that lets the agent revise its investigation path per company — the jump from a fixed chain to *adaptive* reasoning.

### Anti-hallucination summary
Grounding sources, in order of trust: filed EDGAR numbers (point-in-time) > graph `evidence_text` quotes > quantitative metrics (z-scores, ratios) > LLM interpretation. The LLM's job is to *connect* grounded evidence into a thesis, not to supply facts. Every `LongTermSignal` field (thesis, supporting factors, invalidation conditions) must trace to one of these sources.

---

## 5. Schemas (Pydantic, `longterm_agent/schemas.py`)

`PeriodBucketSummary`, `NewsBackgroundProfile`, `BackgroundShift`, `FundamentalsTrend`, `ValuationContext`, `ShiftAnalogs`, `LongTermSignal`, `LongTermAgentOutput` — same structured-output discipline as `news_agent/schemas.py` (see `PYDANTIC_MIGRATION.md`).

---

## 6. Runner, persistence, ops

- `longterm_agent.py`: `LongTermAgent.run(ticker, as_of)` — untraced corpus pre-check, then traced graph (same LangSmith hygiene as `TradingAgent`); LangSmith project `longterm-trading-agent`.
- `run_longterm_agent.py` CLI: `--ticker NVDA --as-of 2026-06-10 --lookback 90`.
- `batch_run_longterm.py`: ticker universe (the tradable-100 list) × weekly dates → `longterm_agent_results`.
- Scheduling: weekly container run (same pattern as `auto_price_fetcher`); Telegram alert via `telegram_bot/` when a signal appears or flips direction.

---

## 7. Evaluation & backtest

- **Simulator:** extend `profit_compute/trade_simulator_yahoo.py` for long horizons — enter at `as_of` close, evaluate at +2w/+4w/+12w, early exit on invalidation; benchmark vs SPY (excess return, not raw).
- **Metrics:** hit rate per horizon, mean excess return, conviction calibration (reuse `calibrate_confidence.py`).
- **Ablations:** background-only vs background+fundamentals vs fundamentals-only — proves each component earns its cost.
- **Golden cases:** 3–5 hand-picked historical tickers with known narrative shifts as regression tests.
- **Unit tests** (pytest, like `news_agent/tests/`): corpus retrieval **time-leakage tests** (nothing after `as_of` — critical, the project already has this discipline via `validate_timestamp`), bucket math, shift metrics on synthetic series, schema validation.

**✅ Test suite built & green (33 passing):** `longterm_agent/tests/` — `test_edgar_normalize` (PIT extraction, restatement-ignore, balance dedup, YTD skip), `test_news_corpus` (word-boundary regex precision, bucketing), `test_fundamentals_usability` (the JPM all-None guard), `test_shift_metrics` (5 synthetic-series), `test_risk_gate` (data-sufficiency guard caps/filters — DB), `test_shift_library` (write→grade→read, leakage + version filters — DB), `test_grade_shifts` (return math on real bars — DB). Pure-logic tests need no DB; integration tests skip cleanly if unreachable.

**✅ LangSmith tracing wired:** `LongTermAgentConfig.enable_tracing()` + `--trace` CLI flag → project `longterm-trading-agent`. Verified via the LangSmith MCP: 3 behavioral runs (NVDA/CAT/JPM) landed as full LangGraph traces (every node, the reflection loop, nested LLM/tool calls), 0 errored root runs. The traces surfaced a real robustness item — the `background_profiling` reduce (NewsBackgroundProfile structured output on the main model) errors intermittently and degrades to "profile unavailable" (non-fatal; detection doesn't depend on it).

---

## 8. Data sufficiency & testing windows (measured 2026-06-12)

### What's actually in the DB

| Data | Coverage | Verdict for this agent |
|---|---|---|
| News | 26,833 articles; dense from **2026-03-01** (~250/day); 22,861 in last 90d | ✅ enough for live runs with 60d lookback today; 90d from ~mid-June |
| Per-company news (90d, ILIKE) | AAPL 357, NVDA 363, MSFT 142, TSLA 134, SBUX 36, **CAT 3** | ✅ mega-cap tech; ❌ non-tech names — excluded by the `MIN_ARTICLES` gate |
| Prices | `stock_prices` empty; 120 `historical_data_<ticker>` tables, 1-min bars **2026-02-23 → now** | ⚠️ ~75 daily bars after aggregation; 180d technicals need the yfinance daily backfill (§3.2) |
| Fundamentals | none | ❌ must build fetcher first (§3.3); yfinance returns ~5 historical quarters immediately, so trend analysis works from day one — only *point-in-time snapshots* need time to accumulate |
| Neo4j graph | 19k Articles / 9.4k Events / 4.8k Stories since ~Feb 2026 | ✅ usable, but needs the precision filter (§3.1) |

### Chosen windows

- **Context (lookback): 60 days now → 90 days target.** With dense news starting 2026-03-01, a 90d window is only fully dense for `as_of ≥ 2026-05-30`; a 60d window is valid for `as_of ≥ 2026-04-30`. Config starts at 60 and moves to 90 once ~3 more weeks of history accrue. Weekly buckets → 8–13 buckets per run.
- **Shift window: 14 days** vs the preceding 46–76 days as baseline.
- **Trade horizon: 4–12 weeks**, evaluated at **+2w and +4w as primary checkpoints** (+12w secondary, becomes measurable later).

### What can be tested, and when

- **Historical backtest is the bottleneck, not live operation.** Valid `as_of` dates with a full 60d window: ~2026-04-30 onward → **~6 weekly cohorts** today. Of those, only cohorts up to mid-May have +4w outcomes already observable. That's enough for smoke-testing the pipeline and obvious-failure detection, **not** for statistically meaningful hit-rate or calibration numbers (~30–50 signals at best, against a need of hundreds).
- **Primary validation = forward (paper) testing.** Run the weekly batch from day one, store every signal in `longterm_agent_results`, and grade cohorts as horizons mature: after 1 month → first +2w/+4w numbers on ~4 fresh cohorts; after 3 months → +4w stats on ~12 cohorts and first +12w reads; meaningful calibration (`calibrate_confidence.py`) realistically needs **a full quarter of forward running**.
- **What to test before that, without waiting:** time-leakage unit tests; shift-metric tests on synthetic series; golden cases picked from Mar–May 2026 news (narrative shifts we can verify by hand); ablation A/B on the same cohort dates. The intraday agent's existing `agent_results`/`simulated_trades` history doesn't transfer — horizons differ.
- **Universe consequence:** start with the ~30–50 tickers that clear `MIN_ARTICLES_FOR_BACKGROUND` over 60d (mega-cap tech skew of the Bloomberg feed); widen only if news sources are added.

---

## 9. Build phases

| Phase | Work | Deliverable / verification |
|---|---|---|
| **0** | ✅ **DONE** — fundamentals table+fetcher (yfinance+EDGAR PIT); `news_corpus_tool.py` (Neo4j AFFECTS + word-boundary regex recall, coverage CLI); `daily_price_fetcher.py` + `stock_prices_daily`; `longterm_agent_results` + `shift_library` SQL; daily+fundamentals wired to weekly scheduler | Verified: corpus coverage per ticker, daily bars writing, fundamentals PIT |
| **1** | ✅ **DONE** — `longterm_agent/` package: config, state, schemas; `corpus_collection` (NO_DATA gate) + `background_profiling` (map-reduce); graph wiring; `run_longterm_agent.py` CLI | Verified: NVDA prints a grounded background profile (6 narratives, sentiment timeline, risks/catalysts); CAT → NO_DATA |
| **2** | ✅ **DONE** — `background_metrics.py` (sentiment z-score, attention spike, tokenized narrative turnover, Neo4j new-story emergence) + `shift_detection` node (metrics anchor → LLM interprets → `BackgroundShift`) | Verified: NVDA caught a real negative cooling (z=-1.08, inflection last 2w); CAT/steady cases correctly abstain; 5/5 synthetic-series tests pass |
| **3** | ✅ **DONE** — `fundamentals_tool.py` (as-of EDGAR statements → revenue/margin/FCF/debt trends, valuation, expectation gap; read-time revenue derivation for filers like NVDA missing the tag) + `technical_tool.py` (180d daily MA/trend/S-R) + `fundamentals_analysis` node (confirm/ignore/contradict verdict vs shift) | Verified: NVDA fundamentals (rev +85%, 75% margin, 151↑/8↓ revisions) read correctly; confirm/contradict verdict in CLI |
| **4** | ✅ **DONE** — deterministic `shift_score` refinement; `synthesis` (news×fundamentals matrix → `LongTermSignal`), `reflection` (bounded loop), `risk_gate` (priced-in/earnings/conviction); full graph + reflection loop; persistence to `longterm_agent_results` + `shift_library` | Verified: NVDA → LONG conviction 0.75 w/ thesis+invalidation, risk gate PASS, reflection loop fires & terminates, result row persisted |
| **4b** | ✅ **DONE** — `similar_shift_analysis` node + `shift_library_tool.py` (write/grade/read, leakage + version filtered) | Verified: write→grade→read round-trip returns analog base rates; cold-start handled |
| **5** | ✅ **DONE** — detection replay (`backfill_shift_library.py`, fast path + `bucket_summary_cache`), `grade_shifts.py`, `backtest_report.py`, AND `full_signal_backtest.py` (whole pipeline → signal → graded vs SPY); corpus + backtest connections hardened; reduce/synthesis retry + fallback | Detection smoke test: 24 runs→15 shifts→17 graded. **Full-signal backtest: 24 runs, 17 traded, 41% win, +0.22% mean excess 4w (median −1.18%).** Both are n=17 single-rally-regime noise — harness + agent behavior validated (discriminates LONG/SHORT/NEUTRAL, risk gate filters 7/24), NOT a strategy verdict. Universe replay + calibration still deferred. |
| **6** | Ops: weekly scheduling, Telegram alerts, Dockerfile | Deployed weekly job |
| **7A** (advanced analysis) | LangChain tools + bounded `deep_analysis` ReAct node (§10), determinism preserved; also fixes LangSmith tool-call visibility | Tool calls + I/O appear in traces; enriched thesis; labels still reproducible |
| **7B** (optional, gated) | Deep Agents harness (§10): planning, subagents incl. red-team, memory, skills | A/B vs Stage-A baseline shows extra reasoning beats it net of token cost |

---

## 10. Advanced-analysis roadmap — LangChain tools → Deep Agents (Phase 7)

The current agent is a **fixed pipeline**: nodes call plain Python functions, the LLM only does structured output at set points. It never *chooses* what to investigate. Giving it that ability is worthwhile — but only if it doesn't wreck the reproducibility the backtest/library depend on. So the governing principle:

### The two approaches

![Add LangChain tools (7A) vs Deep Agents harness (7B)](add_tools_vs_deep_agents.svg)

*(ASCII fallback below for plain-text viewers.)*

**A · Add LangChain tools (7A)** — one node, graph fixed, ~+3–7 calls/run, low risk, do now.

```
  ┌──────────────────────────────────────────────┐
  │ deterministic spine                          │
  │ corpus → shift → fundamentals → analogs      │
  └───────────────────────┬──────────────────────┘
                          v
  ┌──────────────────────────────────────────────┐
  │ deep_analysis · bounded ReAct                │
  │   LLM <-> tools  (bind_tools, <=7 iters)     │
  │   graph · fundamentals · analogs · news      │
  └───────────────────────┬──────────────────────┘
                          v
  ┌──────────────────────────────────────────────┐
  │ synthesis → reflection → risk_gate           │
  └──────────────────────────────────────────────┘
  => labels stay DETERMINISTIC (analyst is advisory only)
```

**B · Deep Agents harness (7B)** — wraps the pipeline, adaptive, ~10× calls/run, gated on a real signal.

```
  ┌──────────────────────────────────────────────┐
  │ orchestrator · planner  (write_todos)        │
  └───────────────────────┬──────────────────────┘
                          v
  ┌──────────────────────────────────────────────┐
  │ subagents (isolated context):                │
  │   - news-background analyst                  │
  │   - fundamentals analyst                     │
  │   - red-team  (attacks the thesis)           │
  └───────────────────────┬──────────────────────┘
                          v
  ┌──────────────────────────────────────────────┐
  │ LangGraph pipeline  (registered as subagent) │
  └──────────────────────────────────────────────┘
  => + memory (Store, cross-run)  + skills (SKILL.md)
```

| | A · add tools (7A) | B · Deep Agents (7B) |
|---|---|---|
| Core change | one new node in the graph | a harness wrapping the pipeline |
| Adaptivity | LLM picks *which tools* | LLM also picks *the plan* |
| Unique to it | — | memory/self-correction, red-team |
| Determinism | preserved (advisory only) | harder (planner + subagents) |
| Cost | ~+3–7 calls/run | ~10× calls/run |
| Effort | small (reuses proven loop) | large (new framework) |
| When | now | only after evidence reasoning is the bottleneck |

They are **sequential, not alternatives** — B's subagents call exactly the tools 7A builds, so A is a prerequisite for B.

### Principle: split the deterministic spine from the adaptive analyst
- **Deterministic spine (stays rule-based, reproducible):** corpus collection, shift metrics → `shift_score`/`has_shifted`, the risk gate, and outcome grading. These produce the **labels** the `shift_library` and backtest rely on. They must NOT depend on any LLM tool-calling, or run-to-run label churn returns.
- **Adaptive analyst (where tools add real value):** the *investigation* that enriches the **thesis** — drilling into fundamentals, checking competitors/sector, traversing causal chains, weighing historical analogs.
- **Hard rule:** tools may change the thesis text and conviction *narrative*, but the deterministic shift label and the graded outcome stay fixed. Backtests run with the analyst OFF (stable labels); live/forward runs turn it ON.

### Stage A — LangChain tools + a bounded `deep_analysis` node (incremental, low-risk)
Wrap the functions that **already exist** as `@tool` and bind them to an LLM in a new ReAct-style node placed after the deterministic shift+fundamentals, before synthesis:
- news: `get_company_news_corpus` / `find_similar_news` (re-query with a refined search)
- graph: `get_causal_chain_with_prices`, `get_story_context`, `get_story_momentum`, `get_events_for_ticker`, `get_cross_ticker_correlation` (company-specific vs sector-wide?)
- fundamentals: `read_fundamentals` + specific-quarter drill-down, `analyze_sector_and_competitors`, `compute_daily_technicals`
- analogs: `find_similar_shifts`

The node runs a **bounded** tool loop (cap iterations like `news_agent`'s `MAX_TOOL_CALL_ITERATIONS=7`) to answer open questions the deterministic pass surfaced ("is this NVDA shift idiosyncratic or sector-wide? what did the 3 closest analogs do? are competitors confirming?") and emits an enriched-analysis dict → synthesis. Benefits: genuinely adaptive depth per company **and** it fixes the observability gap (tool calls + their I/O now appear in LangSmith). Cost: more LLM round-trips; mitigated by the bound + weekly cadence.

### Stage B — Deep Agents harness (Phase 7 proper)
Deep Agents is layered on LangGraph, so the deterministic spine registers as a subagent/tool rather than being rewritten. It adds what Stage A can't:
- **Dynamic planning (`write_todos`)** — the agent plans its investigation per company and revises as it learns, instead of a fixed node order.
- **Filesystem context offloading** — write `background_profile.md`, `fundamentals.md`, `shift_analysis.md`; re-read selectively so a 60–90d analysis isn't context-bound.
- **Subagents with isolated context** — news-analyst, fundamentals-analyst, and a **red-team subagent** that attacks the thesis before synthesis ("what makes this shift meaningless?"). Raw data stays per-subagent; only verdicts reach synthesis.
- **Persistent memory (Store)** — remembers prior theses + realized outcomes per ticker ("last month's negative NVDA call rose 12% — attention-spike misled me") → longitudinal self-correction the stateless pipeline can't do.
- **Skills (SKILL.md)** — editable per-sector analysis playbooks.

Stage A is the prerequisite: the deep-agent subagents are only useful once the tools exist for them to call.

### Honest gating
We have **no real performance baseline yet** (Phase 5 backtest = noise). Adding sophistication before it can be measured is gold-plating. So: **Stage A is worth doing now** (incremental, preserves determinism, fixes tracing). **Stage B (Deep Agents) should be gated** on (a) a meaningful forward-test signal and (b) an A/B showing the Stage-A tools actually improve excess return net of their token cost. Build the measurement before the sophistication.

---

## 11. Risks & open questions

- **News history depth (measured)** — dense news only since 2026-03-01, so backtests are thin today (~6 cohorts); **forward paper-testing is the primary validation path** (§8), with meaningful calibration ~1 quarter out. Importing a news archive is the only way to backtest deep now.
- **Ticker→news mapping is the keystone, and Neo4j is over-broad** (top tickers linked to ~32% of all articles via macro fan-out). Phase 0 must add the precision filter (confidence + company-domain event types + text mention) and validate it before Phase 1.
- **Source bias** — Bloomberg-centric feed → background is reliable for mega-cap tech, thin elsewhere (CAT 3 articles/90d). Universe is effectively the ~30–50 tickers clearing the article-count gate until sources are added.
- **Fundamentals PIT — largely solved for free (§3.3 built).** SEC EDGAR gives true as-filed historical statements (point-in-time via `filed_date`); yfinance covers ratios/estimates and its snapshots build a forward PIT archive. Residual gaps: historical *analyst estimates* aren't PIT, and EDGAR statements are as-originally-reported (no restatement awareness). If a deep backtest later needs clean restated-aware PIT or historical consensus, **FMP Premium (~$59) / Sharadar SF1** are the upgrade — and because the fetcher stores normalized JSONB read by `fundamentals_tool`, swapping the source is a fetcher-only change.
- **Prices need the daily backfill** — `stock_prices` is empty; only 1-min `historical_data_<ticker>` tables exist (~3.7 months). 180d technicals depend on the yfinance daily backfill landing in Phase 0.
- **LLM cost** — 8–13 map calls/ticker/run; mitigated by cheap map model + weekly cadence + caching bucket summaries by `(ticker, week)` (immutable once the week passes).

**Defaults assumed (override if needed):** lookback 60d→90d, shift window 14d, weekly buckets, horizon 4–12 weeks (+2w/+4w checkpoints), universe = tickers clearing the article-count gate.
