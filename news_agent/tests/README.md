# Agent Tools — Integration Tests

Integration tests for all agent tools. Tests run against real DB and Neo4j instances (no mocks). Assertions verify output structure, key presence, and value sanity.

## Run

```bash
# from repo root
pytest
```

`pytest.ini` already sets `testpaths = news_agent/tests`, so no extra arguments are needed.

## Requirements

| Service | Required by |
|---------|-------------|
| PostgreSQL | all DB-dependent tests |
| Neo4j | `test_graph_tools.py` — skipped automatically if unavailable |
| `OPENROUTER_API_KEY` env var | `test_search_events`, `test_find_similar_news` |

Environment variables are loaded from `.env` at repo root via `AgentConfig`.

## Files

| File | What it tests |
|------|---------------|
| `conftest.py` | Session-scoped fixtures: `db_manager`, `known_ticker`, `past_timestamp`, `known_news_id`, `neo4j_manager` |
| `test_tools.py` | All non-graph tools (13 tests) |
| `test_graph_tools.py` | 5 Neo4j graph tools (8 tests) |

## Fixtures (`conftest.py`)

All DB fixtures are **auto-discovered** at runtime — no hardcoded IDs or timestamps needed.

| Fixture | How it's resolved |
|---------|-------------------|
| `db_manager` | `DBManager(AgentConfig.get_db_config())` |
| `known_ticker` | First `historical_data_*` table found in `information_schema.tables` |
| `past_timestamp` | `MAX(timestamp)` from `known_ticker`'s table (guarantees no future leakage) |
| `known_news_id` | Most recent `id` from `news` table |
| `neo4j_manager` | `Neo4jManager()` — test session is skipped if connection fails |

## Tests (`test_tools.py`)

| Test | Tool | Key assertions |
|------|------|----------------|
| `test_validate_valid_ticker` | `validate_ticker` | `valid=True`, `ticker="AAPL"` |
| `test_validate_invalid_ticker` | `validate_ticker` | `valid=False` |
| `test_sentiment_template` | `get_sentiment_template` | correct keys, `intensity` 0–100 |
| `test_get_price_at_time` | `get_price_at_time` | keys: date/open/high/low/close/volume, `close>0` |
| `test_get_historical_prices` | `get_historical_prices` | non-empty DataFrame, required columns, `close>0` |
| `test_get_recent_news_items` | `get_recent_news_items` | list, items have id/title/published |
| `test_get_news_by_id` | `get_news_with_price_impact` | dict, `id` matches input |
| `test_calculate_technical_indicators` | `calculate_technical_indicators` | `trend` in (bullish/bearish/neutral), `rsi` 0–100 |
| `test_detect_volatility_and_volume` | `detect_volatility_and_volume` | keys: volatility_detected, volume_spike_detected, is_hot_stock |
| `test_assess_risk` | `assess_risk` | `risk_score` 0–100, `risk_level` in known set |
| `test_analyze_sector_and_competitors` | `analyze_sector_and_competitors` | has `sector`, `competitors` list |
| `test_find_similar_news` | `find_similar_news` | list, each item has `similarity` 0–1 |
| `test_check_pre_news_movement` | `check_pre_news_movement` | has `suspicious` bool, `flags` list |

## Tests (`test_graph_tools.py`)

Each graph tool is called via `tool.invoke(...)`. Assertion: returns a valid JSON string that parses to a list without raising an exception.

| Test | Tool | Input |
|------|------|-------|
| `test_get_events_for_ticker` | `get_events_for_ticker` | `AAPL`, 30 days |
| `test_get_events_for_ticker_short_window` | `get_events_for_ticker` | `NVDA`, 7 days |
| `test_get_recent_events_no_filter` | `get_recent_events` | limit=10 |
| `test_get_recent_events_with_type` | `get_recent_events` | type=earnings |
| `test_find_related_events` | `find_related_events` | first Event node in Neo4j |
| `test_search_events` | `search_events` | "Apple chip announcement" |
| `test_get_story_context_ticker` | `get_story_context` | "AAPL" |
| `test_get_story_context_domain` | `get_story_context` | "macro_markets" |
