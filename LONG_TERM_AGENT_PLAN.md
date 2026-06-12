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

### 3.1 Company → news corpus retrieval
Today ticker filtering is `ILIKE '%TICKER%'` on title/summary/full_text ([db_manager.py:366](news_agent/database/db_manager.py:366)) — too weak to build a corpus. New function `get_company_news_corpus(ticker, start, end)` that merges three sources and dedupes by `news.id`:
1. **Neo4j (precision):** `MATCH (c:Company {ticker})<-[:AFFECTS]-(e:Event)-[:SOURCED_FROM]->(a:Article)` → `news_id`s, keeping the `AFFECTS.sentiment` per article — this gives free per-article sentiment labels.
2. **pgvector (recall):** similarity search with query `"{company name} {ticker} {known aliases/products}"`.
3. **ILIKE fallback** on company name + ticker.

Before building anything else: **measure coverage** — for 10 sample tickers, how many articles per month does each source return? This decides how much weight the Neo4j path can carry.

### 3.2 Daily price data
`stock_prices` holds 1-minute bars with limited history. Add `stock_prices_daily` (ticker, date, OHLCV) backfilled via yfinance (`period="2y", interval="1d"`) and appended daily by the existing fetcher container. Used for: long-term technicals, priced-in check, and backtesting.

### 3.3 Fundamentals store
New table `fundamentals` + fetcher script (yfinance):

```sql
CREATE TABLE fundamentals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    fetch_date DATE NOT NULL,
    kind VARCHAR(30) NOT NULL,   -- income_q | balance_q | cashflow_q | info | earnings_dates | analyst
    period DATE,                 -- statement period end, NULL for snapshots
    data JSONB NOT NULL,
    UNIQUE (ticker, kind, period, fetch_date)
);
```

Pulled from `yf.Ticker(t)`: `quarterly_financials`, `quarterly_balance_sheet`, `quarterly_cashflow`, `info` (margins, PE/PS/EV-EBITDA, market cap), `earnings_dates`, analyst targets. Cached by `fetch_date` so reruns are reproducible and rate limits aren't hit. **Lookahead rule:** when running with a historical `as_of`, only use statements whose report date (from `earnings_dates`) is before `as_of`.

### 3.4 Results table
`longterm_agent_results`, mirroring `agent_results`: ticker, `as_of`, agent_version, `background_profile` JSONB, `background_shift` JSONB, `fundamentals_summary` JSONB, `signal` JSONB, execution_time.

### 3.5 Verify news history depth
DB docs show news starting 2025-11. A 90-day background needs that much history per ticker — check actual per-ticker article counts first; this also bounds how far back the backtest can go (~Feb 2026 earliest).

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
└── tests/
```

### Graph flow

```
corpus_collection ──► background_profiling ──► shift_detection ──► fundamentals_analysis ──► synthesis ──► reflection ──► risk_gate ──► END
      │                 (Send fan-out over             ▲                      ▲                                  │
      │ <8 articles      weekly buckets)               └──── reflection loop ─┴──────────────────────────────────┘
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
- `fundamentals_tool`: trend over last 4–8 quarters (revenue growth, margins, FCF, debt), valuation vs sector (reuse `sector_competitor_tool`), next earnings date.
- `technical_analysis_tool` on daily data, 180d lookback: trend, 50/200 MA, long-term support/resistance.
- LLM verdict: does the fundamentals trajectory **confirm, ignore, or contradict** the news-background shift? (A negative narrative shift with accelerating revenue is a different trade than one with deteriorating margins.)

**5. `synthesis`** (LLM)
Combine profile + shift + fundamentals into `LongTermSignal`: direction LONG/SHORT/NEUTRAL, conviction 0–1, horizon (e.g. "4–12 weeks"), thesis summary, supporting factors, key risks, and explicit **invalidation conditions** (what observation kills the thesis).

**6. `reflection`** (LLM) — same pattern as existing agent: quality check, may loop to `shift_detection` or `fundamentals_analysis`, `MAX_REFLECTION_ITERATIONS = 1`.

**7. `risk_gate`** (no LLM) — horizon-appropriate replacements for the intraday checks:
- **Priced-in check:** price change over the shift window vs its typical move; if the stock already moved > k× in the signal's direction, cut conviction / flag (long-horizon analog of `PRE_NEWS_PLAYED_OUT_K`).
- **Earnings event risk:** earnings inside the next N days → flag.
- **Data sufficiency:** article counts per bucket, fundamentals freshness.
- Conviction threshold gate → final `filtered_signal`.

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
LOOKBACK_DAYS_BACKGROUND = 90
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

## 5. Schemas (Pydantic, `longterm_agent/schemas.py`)

`PeriodBucketSummary`, `NewsBackgroundProfile`, `BackgroundShift`, `FundamentalsTrend`, `ValuationContext`, `LongTermSignal`, `LongTermAgentOutput` — same structured-output discipline as `news_agent/schemas.py` (see `PYDANTIC_MIGRATION.md`).

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

---

## 8. Build phases

| Phase | Work | Deliverable / verification |
|---|---|---|
| **0** | §3 data foundations: corpus retrieval fn + coverage measurement, daily prices, fundamentals table+fetcher, results table | Scripts + SQL; per-ticker article/price/fundamentals counts printed for 10 tickers |
| **1** | Package skeleton: state, config, schemas; `corpus_collection` + `background_profiling` (map-reduce) | CLI prints a readable background profile for a ticker |
| **2** | `shift_detection`: quantitative metrics + LLM interpretation | CLI shift report; synthetic-series unit tests |
| **3** | `fundamentals_analysis` + long-term technicals | Confirm/contradict verdict visible in CLI output |
| **4** | `synthesis`, `reflection`, `risk_gate`; full graph wiring; persistence to results table | End-to-end run for 3 tickers, traced in LangSmith |
| **5** | Batch runner + backtest + ablations + calibration | Backtest report (hit rate, excess return per horizon) |
| **6** | Ops: weekly scheduling, Telegram alerts, Dockerfile | Deployed weekly job |

---

## 9. Risks & open questions

- **News history depth** — DB started collecting ~Nov 2025; backtest window is short (~Feb 2026 onward). More history accumulates over time; consider importing an archive if backtests need depth now.
- **Ticker→news mapping quality is the keystone** — measure Neo4j `AFFECTS` coverage before Phase 1; if sparse, invest in better article→ticker tagging first.
- **Source bias** — Bloomberg tech feed only → background is reliable for tech megacaps, thin elsewhere. Fine for the current tradable universe; adding sources is a separate project.
- **yfinance fundamentals are not point-in-time** (restated values). Acceptable for v1; flag FMP/AlphaVantage as an upgrade if backtests look promising.
- **LLM cost** — 13 map calls/ticker/run; mitigated by cheap map model + weekly cadence + caching bucket summaries (buckets are immutable once the week is past — cache by `(ticker, week)`).

**To decide (defaults assumed in this plan):** lookback 90d, shift window 14d, weekly buckets, horizon 4–12 weeks, universe = tradable-100 list.
