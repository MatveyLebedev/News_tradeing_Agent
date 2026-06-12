# Agent Tools

LangChain tools used by the trading agent. All tools work with real data from PostgreSQL and Neo4j — no mocks.

Tools come in two forms:
- **Direct functions** — called from agent nodes (e.g. `calculate_technical_indicators`)
- **Factory functions** — `create_*_tool(timestamp, db_manager)` return a `@tool`-decorated LangChain callable bound to a specific article timestamp

## Tools

### Ticker

| File | Function | Description |
|------|----------|-------------|
| `ticker_extraction_tool.py` | `validate_ticker(ticker)` | Validates a US ticker via Yahoo Finance |
| `ticker_extraction_tool.py` | `validate_tickers_batch(tickers_data)` | Validates a list of tickers |
| `ticker_extraction_tool.py` | `create_validate_ticker_tool()` | LangChain `@tool` wrapper |

### Price Data

| File | Function | Description |
|------|----------|-------------|
| `db_query_tool.py` | `get_historical_prices(ticker, timestamp, lookback_days, db_manager)` | OHLCV DataFrame for a lookback window ending at `timestamp` |
| `db_query_tool.py` | `get_price_at_time(ticker, timestamp, db_manager)` | Single closest price record before `timestamp` |
| `db_query_tool.py` | `create_get_price_data_tool(timestamp, db_manager)` | LangChain `@tool` (3-day, 15-min resampled) |
| `db_query_tool.py` | `create_get_price_at_time_tool(timestamp, db_manager)` | LangChain `@tool` |

### News

| File | Function | Description |
|------|----------|-------------|
| `get_recent_news.py` | `get_recent_news_items(timestamp, db_manager, hours, ticker)` | Recent news articles before `timestamp` |
| `get_recent_news.py` | `create_get_recent_news_tool(timestamp, db_manager)` | LangChain `@tool` |
| `get_news_by_id.py` | `get_news_with_price_impact(news_id, db_manager)` | News article by ID with market-hours flag |

### Sentiment

| File | Function | Description |
|------|----------|-------------|
| `sentiment_analysis_tool.py` | `get_sentiment_template()` | Returns example sentiment dict structure |

### Technical Analysis

| File | Function | Description |
|------|----------|-------------|
| `technical_analysis_tool.py` | `calculate_technical_indicators(ticker, price_data)` | RSI, ATR, MAs, Bollinger Bands, support/resistance |
| `technical_analysis_tool.py` | `create_get_technical_indicators_tool(timestamp, db_manager)` | LangChain `@tool` |

### Volatility & Volume

| File | Function | Description |
|------|----------|-------------|
| `volatility_volume_detector_tool.py` | `detect_volatility_and_volume(ticker, timestamp, db_manager)` | Detects volatility spikes and volume anomalies vs 30-day average |

### Risk Assessment

| File | Function | Description |
|------|----------|-------------|
| `risk_assessment_tool.py` | `assess_risk(ticker, timestamp, db_manager, sentiment_data, technical_indicators)` | Composite 0–100 risk score from volatility, technicals, sentiment, sector |
| `risk_assessment_tool.py` | `create_check_risk_tool(timestamp, db_manager)` | LangChain `@tool` |

### Sector & Competitors

| File | Function | Description |
|------|----------|-------------|
| `sector_competitor_tool.py` | `analyze_sector_and_competitors(ticker, timestamp, db_manager)` | Sector, industry, competitor tickers via Yahoo Finance + DB |
| `sector_competitor_tool.py` | `create_analyze_sector_tool(timestamp, db_manager)` | LangChain `@tool` |

### Similar News & Patterns

| File | Function | Description |
|------|----------|-------------|
| `find_similar_news_tool.py` | `find_similar_news(news_text, timestamp, db_manager, top_k)` | pgvector similarity search |
| `find_similar_news_tool.py` | `find_similar_news_with_price_impact(ticker, search_query, timestamp, db_manager)` | Similar news + 40-min price impact |
| `find_similar_news_tool.py` | `create_find_similar_news_tool(timestamp, db_manager)` | LangChain `@tool` |
| `find_similar_news_tool.py` | `create_find_similar_news_with_price_tool(timestamp, db_manager)` | LangChain `@tool` |
| `historical_pattern_tool.py` | `find_similar_historical_news(news_text, ticker, timestamp, db_manager)` | Similar news + 40-min price movement |

### Ticker Analysis

| File | Function | Description |
|------|----------|-------------|
| `ticker_analysis_tool.py` | `analyze_ticker_with_similar_news(ticker, news_text, timestamp, db_manager)` | Similar news + price statistics + pattern summary |
| `ticker_analysis_tool.py` | `get_ticker_historical_context(ticker, timestamp, db_manager, lookback_days)` | 7-day price stats |
| `ticker_analysis_tool.py` | `create_ticker_analysis_tool(timestamp, db_manager, news_text)` | LangChain `@tool` |
| `ticker_analysis_tool.py` | `create_ticker_historical_context_tool(timestamp, db_manager)` | LangChain `@tool` |

### Pre-News Movement

| File | Function | Description |
|------|----------|-------------|
| `pre_news_movement_tool.py` | `check_pre_news_movement(ticker, news_timestamp, db_manager, window_minutes)` | Detects suspicious price/volume moves in the 60 min before news (insider leak indicator) |
| `pre_news_movement_tool.py` | `check_multiple_tickers_pre_news(tickers, news_timestamp, db_manager)` | Same check for multiple tickers |

### Graph Tools (Neo4j)

| File | Function | Description |
|------|----------|-------------|
| `graph_tools.py` | `create_graph_tools(neo4j_manager, db_manager, timestamp)` | Returns 5 LangChain tools backed by Neo4j + pgvector |

The 5 graph tools:

| Tool name | Description |
|-----------|-------------|
| `get_events_for_ticker` | Events linked to a company ticker (last N days) |
| `get_recent_events` | Most recent events, optionally filtered by type |
| `find_related_events` | Cause/effect chain from a given event (up to depth N) |
| `search_events` | Semantic search via pgvector → Neo4j event lookup |
| `get_story_context` | Ongoing Story narrative for a ticker or macro domain |

## Key design rules

- All price/news queries filter strictly to data **before** `timestamp` to prevent data leakage.
- Timestamps are always converted to **US Eastern Time** (`America/New_York`) internally.
- `create_*_tool` factories bind `timestamp` and `db_manager` at construction time; the returned tool only exposes ticker/query parameters to the LLM.
- Tools return `None` / empty list / error dict rather than raising, so the agent can handle missing data gracefully.
