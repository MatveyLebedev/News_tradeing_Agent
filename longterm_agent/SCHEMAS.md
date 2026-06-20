# Long-Term Agent — Schemas Reference

Canonical schemas for the long-term trading agent: the pipeline, the LangGraph state, the
Pydantic node outputs, and the database tables. Prose docs are in [README.md](README.md);
design rationale in [../LONG_TERM_AGENT_PLAN.md](../LONG_TERM_AGENT_PLAN.md).

---

## 1. Pipeline schema

![Long-term agent pipeline schema](pipeline_schema.png)

<details><summary>ASCII fallback</summary>

```
INPUT: ticker + as_of (+ lookback_days)
   │
   ▼
corpus_collection ──(articles < MIN_ARTICLES)──► NO_DATA → END   → news_corpus · buckets
   ▼
background_profiling   map (per-week, cached) → reduce          → NewsBackgroundProfile
   ▼
shift_detection        metrics → DETERMINISTIC shift_score      → BackgroundShift
   ▼
fundamentals_analysis  PIT statements + valuation + gap         → FundamentalsTrend
   ▼
similar_shift_analysis shift_library analogs (leakage filtered) → ShiftAnalogs
   ▼
[deep_analysis]        ⏳ planned (7A) — bounded ReAct, advisory
   ▼
synthesis              news×fundamentals matrix                 → LongTermSignal
   ▼  ▲ reflection loop (≤ MAX_REFLECTION_ITERATIONS)
reflection             quality check → loop or proceed          → ReflectionOutput
   ▼
risk_gate              priced-in · earnings · data-sufficiency  → filtered_signal
   ▼
END → persist (longterm_agent_results + shift_library if shifted)
```
</details>

The build-vs-planned (7A/7B) view: [add_tools_vs_deep_agents.svg](add_tools_vs_deep_agents.svg).

---

## 2. LangGraph state — `LongTermAgentState` (`state.py`)

`TypedDict, total=False`. Each node reads upstream keys and writes its own.

| Key | Type | Written by |
|---|---|---|
| `ticker` | str | input |
| `company_name` | str | input (optional) |
| `as_of` | datetime | input — the analysis date; **everything ≤ as_of** |
| `lookback_days` | int | input (default 60) |
| `shift_window_days` | int | input (default 14) |
| `messages` | List[dict] | all nodes (reasoning chain, LangChain-native) |
| `news_corpus` | List[dict] | corpus_collection — `{id, published, title, summary, affects_sentiment?, sources}` |
| `corpus_provenance` | dict | corpus_collection — `{graph, ilike, both, total}` |
| `period_buckets` | List[dict] | corpus_collection — weekly buckets |
| `article_count` | int | corpus_collection |
| `no_data` | bool | corpus_collection — True → early NO_DATA exit |
| `bucket_summaries` | List[dict] | background_profiling (map outputs) |
| `background_profile` | dict | background_profiling (reduce → `NewsBackgroundProfile`) |
| `background_metrics` | dict | shift_detection (quantitative metrics) |
| `background_shift` | dict | shift_detection (`BackgroundShift`) |
| `fundamentals` | dict | fundamentals_analysis (reader output + `trend`) |
| `technical_longterm` | dict | fundamentals_analysis (daily technicals) |
| `shift_analogs` | dict | similar_shift_analysis (`ShiftAnalogs`) |
| `signal` | dict | synthesis (`LongTermSignal`) |
| `reflection_count` | int | reflection |
| `needs_refinement` | bool | reflection |
| `loop_to` | str | reflection |
| `risk_flags` | List[str] | risk_gate |
| `risk_checks` | dict | risk_gate (`{priced_in, earnings}`) |
| `filtered_signal` | Optional[dict] | risk_gate — **final output** (None = filtered) |

---

## 3. Pydantic node-output schemas (`schemas.py`)

### `PeriodBucketSummary` — background_profiling map step (one weekly bucket)
| Field | Type | Notes |
|---|---|---|
| `themes` | List[str] | dominant themes this week |
| `key_events` | List[str] | concrete events, grounded in articles |
| `sentiment` | float | net sentiment, −1..1 |
| `attention` | `low\|normal\|elevated\|high` | |
| `notable_article_ids` | List[int] | |

### `NewsBackgroundProfile` — reduce step (THE news background)
| Field | Type |
|---|---|
| `summary` | str (2–4 sentences) |
| `dominant_narratives` | List[`NarrativeThread`] |
| `sentiment_timeline` | List[float] (oldest→newest) |
| `attention_timeline` | List[str] |
| `recurring_risks` | List[str] |
| `recurring_catalysts` | List[str] |

`NarrativeThread`: `{label: str, direction: positive\|negative\|mixed\|neutral, weeks_active: int, evidence: List[str]}`

### `BackgroundShift` — shift_detection
| Field | Type | Notes |
|---|---|---|
| `has_shifted` | bool | **deterministic** (from metrics, not LLM) |
| `direction` | `positive\|negative\|null` | deterministic (sign of delta) |
| `magnitude_z` | float? | sentiment z-score |
| `shift_score` | float 0–1 | **deterministic** continuous strength |
| `inflection_date` | str? | LLM-refined |
| `candidate_catalysts` | List[str] | LLM characterization |
| `reasoning` | str | LLM characterization |

### `FundamentalsTrend` — fundamentals_analysis
| Field | Type |
|---|---|
| `revenue_growth_trend` / `margin_trend` / `fcf_trend` / `debt_trend` | str? |
| `verdict` | `confirms\|ignores\|contradicts\|unknown` |
| `reasoning` | str |

`ValuationContext`: `{pe?, ps?, ev_ebitda?, vs_sector?}`

### `ShiftAnalogs` — similar_shift_analysis
| Field | Type |
|---|---|
| `n_analogs` | int |
| `median_fwd_return_4w` | float? |
| `share_positive` | float? |
| `fundamentals_matched_note` | str |
| `nearest_cases` | List[str] |

### `LongTermSignal` — synthesis (the product)
| Field | Type |
|---|---|
| `direction` | `LONG\|SHORT\|NEUTRAL` |
| `conviction` | float 0–1 |
| `horizon` | str (e.g. "4–12 weeks") |
| `thesis` | str |
| `supporting_factors` / `key_risks` / `invalidation_conditions` | List[str] |

### `ReflectionOutput` — reflection
`{needs_refinement: bool, loop_to: shift_detection\|fundamentals_analysis\|null, advice: str, confidence_in_analysis: float}`

### `LongTermAgentOutput` — top-level wrapper
`{ticker: str, as_of: str, verdict: str, signal: LongTermSignal?}`

---

## 4. Database schemas

![Long-term agent data model](data_model_schema.png)

All tables live in Postgres `trade_agent`. "Fetchers write, agent reads." Canonical DDL is
in the `create_*.sql` files; summaries below.

### `fundamentals` — EDGAR PIT statements + yfinance snapshots ([create_fundamentals_table.sql](../bloomberg_news_fetcher/create_fundamentals_table.sql))
| Column | Type | Notes |
|---|---|---|
| `ticker` | varchar | |
| `source` | varchar | `edgar` \| `yfinance` |
| `kind` | varchar | edgar: `income_statement\|balance_sheet\|cash_flow`; yf: `info\|earnings_dates\|analyst_estimates\|eps_revisions` |
| `period_end` | date? | statement period (null for snapshots) |
| `fiscal_period` | varchar? | `Q1..Q4\|FY` |
| `filed_date` | date? | **EDGAR PIT key** — read with `filed_date ≤ as_of` |
| `fetch_date` | date | **yfinance PIT key** — read with `fetch_date ≤ as_of` |
| `dedup_key` | text UNIQUE | idempotent upsert target |
| `data` | jsonb | normalized payload |

### `stock_prices_daily` — daily OHLCV ([create_daily_price_table.sql](../bloomberg_news_fetcher/create_daily_price_table.sql))
`(ticker, date)` unique. Columns: `ticker, date, open, high, low, close, adj_close, volume, fetch_time`. Used for long-term technicals, priced-in check, outcome grading (incl. `SPY` benchmark).

### `shift_library` — analogical case base ([create_shift_library_table.sql](create_shift_library_table.sql))
| Column | Type | Notes |
|---|---|---|
| `agent_name` / `agent_version` | varchar | version-scoped; regenerate under new version |
| `ticker` / `as_of` | varchar / date | |
| `inflection_date` | date? | |
| `direction` | varchar | positive \| negative |
| `magnitude_z` / `shift_score`(in `features`) | real | fingerprint |
| `shift_window_days` | int | |
| `causal_event_types` | text[] | new-story labels |
| `attention_spike_ratio` | real | |
| `fundamentals_context` | jsonb | verdict + valuation snapshot |
| `shift_embedding` | vector(1536) | reserved for semantic analog search |
| `features` | jsonb | extensible (shift_score, turnover) |
| `fwd_return_2w/4w/12w`, `benchmark_return_4w` | real? | **backfilled by grade_shifts** |
| `outcome_filled_at` | date? | null = ungraded |
| UNIQUE | `(agent_version, ticker, as_of)` | |

### `bucket_summary_cache` — per-week map cache ([create_bucket_cache_table.sql](create_bucket_cache_table.sql))
PK `(ticker, week_start, agent_version)`, `data jsonb`. Makes the replay cheap + reproducible (each `(ticker, week)` summarized once). Only **complete** weeks are cached.

### `longterm_agent_results` — run results ([create_results_table.sql](create_results_table.sql))
`(agent_version, ticker, as_of)` unique. Stores `verdict`, `conviction`, `article_count`,
`execution_time_seconds`, the reasoning artifacts (`background_profile`, `background_shift`,
`fundamentals_summary`, `shift_analogs`, `signal` as jsonb), and the gradable
`fwd_return_4w` / `benchmark_return_4w` / `outcome_filled_at`.

### External (read-only)
- **`news`** — Bloomberg/CNBC articles + `embedding` (pgvector). Read via publication date.
- **Neo4j graph** — `Article → Event → Story`, `AFFECTS{sentiment, confidence}`, `CAUSES`. Read via `news_agent`'s `create_graph_tools` (as-of aware).
