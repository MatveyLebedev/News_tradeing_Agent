# Enhanced Agent Features - LLM-Driven Tool Usage

## Summary of Enhancements

The agent now supports **LLM-driven tool usage** in the main analysis node, allowing dynamic and contextual tool calls.

## Key Enhancements

### 1. LLM-Generated Search Queries

**Tool**: `find_similar_news(search_query, timestamp, db_manager, top_k)`

**Before**: Only used in base_context with fixed query (news text)

**After**: LLM can generate custom search queries in main_analysis based on:
- Specific events: "earnings beat", "product recall", "CEO change"
- Contextual patterns: "Apple chip performance breakthrough M1 M2 M3"
- Competitive analysis: "Apple Microsoft competition AI technology"
- Sector events: "chip shortage semiconductor supply"

**Example**:
```python
# LLM analyzes news about Apple M4 chip
# LLM decides: "I should search for similar chip announcements"
search_query = "Apple chip performance breakthrough M1 M2 M3 announcement"
similar_news = find_similar_news(search_query, timestamp, db_manager, top_k=5)
# Returns: M1 (+3.2%), M2 (+2.8%), M3 (+4.1%) announcements
```

---

### 2. Ticker-Specific News Filtering

**Tool**: `get_recent_news_items(timestamp, db_manager, hours, ticker, limit)`

**Before**: Only used in base_context for all news (no ticker filter)

**After**: LLM can fetch news for specific tickers during analysis:
- Check if ticker had other news today
- Analyze competitor news
- Get sector-wide context
- Adjust time windows per ticker

**Example**:
```python
# LLM is analyzing AAPL
# LLM decides: "Check if AAPL had other news today"
aapl_recent = get_recent_news_items(
    timestamp, db_manager, hours=24, ticker="AAPL", limit=10
)
# Finds 3 other AAPL news items today
# LLM concludes: "AAPL already had 3 news, may be overheated"
```

---

## Use Cases

### Use Case 1: Event-Specific Historical Analysis

**Scenario**: News says "Apple beats earnings by 15%"

**LLM Actions**:
1. Generates query: "Apple earnings beat revenue surprise positive"
2. Calls `find_similar_news(query, timestamp, db_manager, top_k=10)`
3. Analyzes historical earnings beats: typically 2-5% gains
4. Generates signal with historical context

---

### Use Case 2: Competitive Context

**Scenario**: News mentions both Apple and Microsoft

**LLM Actions**:
1. Gets AAPL recent news: `get_recent_news_items(..., ticker="AAPL", limit=5)`
2. Gets MSFT recent news: `get_recent_news_items(..., ticker="MSFT", limit=5)`
3. Searches competitive news: `find_similar_news("Apple Microsoft competition", ...)`
4. Comprehensive competitive analysis

---

### Use Case 3: Sector-Wide Event

**Scenario**: News about chip shortage

**LLM Actions**:
1. Identifies affected tickers: AAPL, NVDA, AMD, INTC
2. For each ticker: `get_recent_news_items(..., ticker=X, hours=72)`
3. Searches sector patterns: `find_similar_news("chip shortage semiconductor", ...)`
4. Sector-wide impact assessment

---

### Use Case 4: Hot Stock Detection

**Scenario**: Analyzing TSLA

**LLM Actions**:
1. Checks recent TSLA news: `get_recent_news_items(..., ticker="TSLA", hours=24)`
2. Finds 5 news items in last 24h
3. Searches similar volatility: `find_similar_news("Tesla high volatility news", ...)`
4. Decides: "TSLA too hot, reduce confidence"

---

### Use Case 5: Product Launch Analysis

**Scenario**: New iPhone announcement

**LLM Actions**:
1. Searches previous launches: `find_similar_news("Apple iPhone launch announcement", ...)`
2. Checks competitor reactions: `get_recent_news_items(..., ticker="SSNLF", hours=48)` (Samsung)
3. Analyzes historical launch impacts
4. Generates signal with launch pattern context

---

## Benefits

### 1. **Contextual Intelligence**
- LLM generates queries based on news content
- Not limited to exact text matching
- Can search for concepts and patterns

### 2. **Focused Analysis**
- Get news for specific tickers only
- Avoid noise from irrelevant news
- Adjust time windows per situation

### 3. **Dynamic Adaptation**
- LLM decides what to search for
- Can search multiple times with different queries
- Iteratively refines search based on findings

### 4. **Better Signal Quality**
- Historical context for specific events
- Competitive landscape awareness
- Sector-wide impact consideration
- Hot stock detection

---

## Implementation Details

### Main Analysis Node Enhancement

The `main_analysis_node` now documents LLM-driven tool usage:

```python
@traceable(name="main_analysis_node", run_type="chain")
def main_analysis_node(state, llm, db_manager, config):
    """
    LLM can dynamically use tools:
    - find_similar_news(search_query, ...) with LLM-generated queries
    - get_recent_news_items(..., ticker="X", ...) for ticker-specific news
    """
```

### Tool Signatures

**find_similar_news**:
```python
def find_similar_news(
    news_text: str,  # Can be LLM-generated query
    timestamp: datetime,
    db_manager: Any,
    top_k: int = 10
) -> List[Dict[str, Any]]
```

**get_recent_news_items**:
```python
def get_recent_news_items(
    timestamp: datetime,
    db_manager: Any,
    hours: int = 24,
    ticker: Optional[str] = None,  # NEW: Ticker filter
    limit: int = None  # NEW: Result limit
) -> List[Dict[str, Any]]
```

---

## Configuration

```python
# Main analysis can use these dynamically
SIMILAR_NEWS_TOP_K = 10  # Default for similar news
RECENT_NEWS_HOURS = 24  # Default time window
RECENT_NEWS_LIMIT = 10  # Default result limit

# LLM can override these per call
# Example: hours=48 for slower-moving stocks
# Example: top_k=5 for focused search
```

---

## Schema Updates

### AGENT_SCHEMA.md
- ✅ Updated Node 3 (Main Analysis) with new tools
- ✅ Added "LLM-Driven Tool Usage Examples" section
- ✅ Added 5 detailed use cases
- ✅ Updated Key Features Summary

### agent_enhanced_schema.png
- ✅ Visual diagram showing enhanced tools
- ✅ Highlighted LLM-driven tools in yellow
- ✅ Example query flow visualization
- ✅ Enhanced features callout

### nodes.py
- ✅ Updated main_analysis_node docstring
- ✅ Documented LLM-driven tool capabilities

---

## Comparison: Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| **Similar News** | Fixed query (news text) | LLM-generated queries |
| **Recent News** | All tickers | Ticker-specific filtering |
| **Search Scope** | Broad, unfocused | Contextual, targeted |
| **Time Windows** | Fixed (24h) | Dynamic (LLM decides) |
| **Result Limits** | No limit | Configurable per call |
| **Intelligence** | Static | Adaptive |

---

## Future Enhancements

Potential future improvements:

1. **Multi-step Search**: LLM can search, analyze results, then search again
2. **Cross-ticker Analysis**: Compare news patterns across multiple tickers
3. **Temporal Analysis**: Search different time periods based on event type
4. **Sentiment-based Search**: "positive earnings news" vs "negative earnings news"
5. **Volume-based Filtering**: Get news only for high-volume days

---

## Summary

The agent now has **intelligent, context-aware tool usage** where:
- ✅ LLM generates search queries based on analysis needs
- ✅ LLM filters news by ticker for focused analysis
- ✅ LLM adjusts parameters (time windows, limits) dynamically
- ✅ Tools provide exactly what LLM needs, when it needs it

This makes the agent significantly more powerful and adaptive! 🚀

