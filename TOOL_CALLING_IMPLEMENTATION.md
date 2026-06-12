# LangChain Tool Calling Implementation

## Overview

The `main_analysis_node` now uses **LangChain's native tool calling** feature, allowing the LLM to dynamically call tools during analysis to gather additional information.

## How It Works

### 1. Tool Wrapper Creation

Tools are wrapped with `@tool` decorator and capture context (`db_manager`, `timestamp`) from the node:

```python
@tool
def find_similar_news_tool(search_query: str, ticker: str = None, top_k: int = 10) -> str:
    """
    Find similar historical news articles based on a search query.
    """
    # Uses captured timestamp and db_manager from node scope
    results = find_similar_news(
        news_text=search_query,
        timestamp=timestamp,  # Captured from node
        db_manager=db_manager,  # Captured from node
        top_k=top_k
    )
    return json.dumps(results, default=str)
```

### 2. Tool Binding

Tools are bound to the LLM using `.bind_tools()`:

```python
tools = [find_similar_news_tool, get_recent_news_tool]
llm_with_tools = llm.bind_tools(tools)
```

### 3. Tool Calling Loop

The node implements an iterative loop that:
1. Invokes LLM with current messages
2. Checks if LLM wants to call tools
3. Executes tools if requested
4. Adds tool results back to messages
5. Continues until LLM returns final response

```python
max_iterations = 5
iteration = 0

while iteration < max_iterations:
    iteration += 1
    response = llm_with_tools.invoke(messages_to_send)
    messages_to_send.append(response)
    
    # Check for tool calls
    if hasattr(response, 'tool_calls') and response.tool_calls:
        # Execute tools
        tool_messages = []
        for tool_call in response.tool_calls:
            tool_result = tool.invoke(tool_call['args'])
            tool_messages.append(
                ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tool_call['id']
                )
            )
        
        # Add tool results and continue
        messages_to_send.extend(tool_messages)
        continue
    
    # No tool calls - final response
    break
```

## Available Tools

### 1. `find_similar_news_tool`

**Purpose**: Find similar historical news with price impact data

**Parameters**:
- `search_query` (str): Text query to find similar news (e.g., "Apple chip breakthrough")
- `ticker` (str, optional): Filter by ticker symbol (e.g., "AAPL")
- `top_k` (int, default=10): Number of results to return

**Example Usage**:
```python
find_similar_news_tool(
    search_query="Apple announces new chip performance",
    ticker="AAPL",
    top_k=5
)
```

**Returns**: JSON string with similar news items including:
- News title, summary, publication date
- Similarity score (0-1)
- Price impact data (change %, direction)

### 2. `get_recent_news_tool`

**Purpose**: Get recent news articles for a ticker

**Parameters**:
- `ticker` (str, optional): Filter by ticker symbol
- `hours` (int, default=24): Hours to look back
- `limit` (int, default=10): Maximum number of results

**Example Usage**:
```python
get_recent_news_tool(
    ticker="AAPL",
    hours=48,
    limit=5
)
```

**Returns**: JSON string with recent news items

### 3. `get_price_data_tool`

**Purpose**: Get historical price data (OHLCV) for a ticker

**Parameters**:
- `ticker` (str): Stock ticker symbol
- `lookback_days` (int, default=30): Number of days to look back

**Example Usage**:
```python
get_price_data_tool("AAPL", lookback_days=30)
```

**Returns**: JSON string with historical price data (date, open, high, low, close, volume)

### 4. `get_price_at_time_tool`

**Purpose**: Get the current price at the news timestamp

**Parameters**:
- `ticker` (str): Stock ticker symbol

**Example Usage**:
```python
get_price_at_time_tool("AAPL")
```

**Returns**: JSON string with price data (open, high, low, close, volume) at timestamp

### 5. `get_technical_indicators_tool`

**Purpose**: Get technical indicators (RSI, ATR, Moving Averages, Bollinger Bands)

**Parameters**:
- `ticker` (str): Stock ticker symbol
- `lookback_days` (int, default=30): Number of days to look back

**Example Usage**:
```python
get_technical_indicators_tool("AAPL", lookback_days=30)
```

**Returns**: JSON string with technical indicators:
- RSI (Relative Strength Index)
- ATR (Average True Range)
- Moving Averages (short/long)
- Bollinger Bands
- Support/Resistance levels

### 6. `analyze_sector_tool`

**Purpose**: Analyze sector and competitor stocks

**Parameters**:
- `ticker` (str): Stock ticker symbol
- `max_competitors` (int, default=3): Maximum number of competitors to analyze

**Example Usage**:
```python
analyze_sector_tool("AAPL", max_competitors=3)
```

**Returns**: JSON string with:
- Sector and industry information
- Competitor analysis with price changes
- Sector trend (bullish/bearish/neutral)
- Average sector performance

### 7. `check_risk_tool`

**Purpose**: Assess risk based on sentiment and technical indicators

**Parameters**:
- `ticker` (str): Stock ticker symbol
- `sentiment_intensity` (int): Sentiment intensity score (0-100)
- `sentiment_type` (str): "bullish", "bearish", or "neutral"
- `rsi` (float, optional): RSI value if available
- `atr` (float, optional): ATR value if available

**Example Usage**:
```python
check_risk_tool(
    "AAPL",
    sentiment_intensity=85,
    sentiment_type="bullish",
    rsi=65.5,
    atr=2.3
)
```

**Returns**: JSON string with:
- Risk score (0-100, higher = more risky)
- Risk level (low/medium/high)
- Risk factors and reasoning

### 8. `validate_ticker_tool`

**Purpose**: Validate ticker symbol (US market only)

**Parameters**:
- `ticker` (str): Stock ticker symbol to validate

**Example Usage**:
```python
validate_ticker_tool("AAPL")
```

**Returns**: JSON string with validation result:
- `valid` (bool): Whether ticker is valid
- `reason` (str): Validation reason

## Message Flow

### Example Conversation:

```
[SYSTEM] "Execute analysis plan. Use tools if needed..."

[AI] "I'll search for similar Apple chip news to understand historical impact"
     tool_calls: [
       {
         "name": "find_similar_news_tool",
         "args": {"search_query": "Apple chip breakthrough", "ticker": "AAPL"}
       }
     ]

[TOOL] "[{similar news data with price impact}]"

[AI] "Based on similar news, I'll also check recent AAPL announcements"
     tool_calls: [
       {
         "name": "get_recent_news_tool",
         "args": {"ticker": "AAPL", "hours": 24}
       }
     ]

[TOOL] "[{recent news data}]"

[AI] "Based on the gathered information, here's my analysis:
      {
        'tickers': ['AAPL'],
        'sentiment': {...},
        'signals': [...]
      }"
```

## Benefits

### 1. **Dynamic Information Gathering**
- LLM can decide when to gather more information
- No need to pre-fetch all possible data
- More efficient and focused queries

### 2. **Context-Aware Tool Usage**
- LLM uses tools based on what it discovers
- Can refine queries based on initial results
- Iterative refinement of analysis

### 3. **Better Analysis Quality**
- LLM has access to historical patterns
- Can check for recent related news
- More informed trading decisions

### 4. **Transparency**
- All tool calls are tracked in message history
- Easy to debug and understand LLM reasoning
- Full audit trail

## Implementation Details

### Tool Execution

Tools are executed synchronously in the loop:
- Each tool call is executed immediately
- Results are added as `ToolMessage` objects
- Tool call ID links request to response

### Error Handling

```python
try:
    tool_result = tool.invoke(tool_args)
except Exception as e:
    tool_result = json.dumps({"error": str(e)})
    print(f"✗ Error: {e}")
```

### Message Tracking

All tool calls are tracked in state:
```python
add_message(
    state,
    "assistant",
    f"Used {len(tool_calls_made)} tool(s)",
    name="main_analysis",
    data={"tool_calls": tool_calls_made}
)
```

### Iteration Limit

Maximum 5 iterations to prevent infinite loops:
```python
max_iterations = 5
```

## Usage Examples

### Scenario 1: LLM Needs Historical Context

```
1. LLM receives news about "Apple M4 chip"
2. LLM calls: find_similar_news_tool("Apple chip announcement", "AAPL")
3. Gets historical price impact data
4. Uses data to inform sentiment analysis
5. Returns final analysis with confidence
```

### Scenario 2: LLM Checks for Related News

```
1. LLM identifies ticker "MSFT"
2. LLM calls: get_recent_news_tool("MSFT", hours=48)
3. Discovers other recent Microsoft announcements
4. Adjusts analysis to account for multiple news items
5. Returns refined signals
```

### Scenario 3: Multi-Tool Analysis

```
1. LLM calls find_similar_news_tool for AAPL
2. Gets results showing bullish pattern
3. LLM calls get_recent_news_tool to check for other news
4. Finds conflicting bearish news
5. LLM adjusts confidence and reasoning
6. Returns balanced analysis
```

### Scenario 4: Comprehensive Analysis with All Tools

```
1. LLM identifies ticker "AAPL" from news
2. LLM calls validate_ticker_tool("AAPL") → Valid
3. LLM calls get_price_at_time_tool("AAPL") → Gets current price
4. LLM calls get_technical_indicators_tool("AAPL") → Gets RSI, ATR, etc.
5. LLM calls analyze_sector_tool("AAPL") → Gets sector trends
6. LLM calls find_similar_news_tool("Apple chip", "AAPL") → Gets historical patterns
7. LLM calls check_risk_tool("AAPL", sentiment_intensity=85, sentiment_type="bullish", rsi=65.5)
8. LLM synthesizes all data and returns comprehensive analysis with signals
```

## Configuration

### Max Iterations

Default: 5 iterations
- Can be adjusted based on complexity needs
- Prevents infinite loops
- Usually 1-2 iterations sufficient

### Available Tools

Currently **8 tools** available:
1. `find_similar_news_tool` - Find similar historical news
2. `get_recent_news_tool` - Get recent news articles
3. `get_price_data_tool` - Get historical price data (OHLCV)
4. `get_price_at_time_tool` - Get price at news timestamp
5. `get_technical_indicators_tool` - Get technical indicators (RSI, ATR, MAs, BB)
6. `analyze_sector_tool` - Sector and competitor analysis
7. `check_risk_tool` - Risk assessment
8. `validate_ticker_tool` - Validate ticker symbols

All tools are fully integrated and ready to use!

## Future Enhancements

Potential additions:
- `get_news_by_id_tool` - Get specific news article with price impact
- `check_pre_news_movement_tool` - Detect suspicious pre-news price movements
- `detect_volatility_tool` - Volatility and volume spike detection
- `get_competitor_news_tool` - Get news for competitor tickers

## Summary

✅ **LLM can now call 8 tools dynamically**  
✅ **Tools capture node context automatically**  
✅ **Iterative tool calling loop**  
✅ **Full message history tracking**  
✅ **Error handling and iteration limits**  
✅ **Transparent tool usage**  
✅ **Comprehensive tool suite**:
   - News analysis (similar, recent)
   - Price data (historical, current)
   - Technical analysis (RSI, ATR, MAs, BB)
   - Sector analysis (competitors, trends)
   - Risk assessment
   - Ticker validation

The agent is now more intelligent and can gather comprehensive information on-demand! 🚀

