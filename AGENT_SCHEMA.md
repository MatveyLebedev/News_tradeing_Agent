# Trading Agent Schema - Message-Based Architecture

## Overview

The agent uses a message-based architecture with 5 nodes that process news through planning, analysis, reflection, and risk assessment.

## Graph Flow

```
START
  ↓
┌─────────────────┐
│ base_context    │ - Retrieves 10 recent news + 10 similar news
└────────┬────────┘
         ↓
┌─────────────────┐
│ planning        │ - LLM creates customized action plan
└────────┬────────┘
         ↓
┌─────────────────┐
│ main_analysis   │ - Executes plan (can deviate)
└────────┬────────┘
         ↓
┌─────────────────┐
│ reflection      │ - Reviews analysis quality
└────────┬────────┘
         ↓
    [Decision]
         ├─→ needs_refinement=True, loop_to="planning" → LOOP to planning
         ├─→ needs_refinement=True, loop_to="main_analysis" → LOOP to main_analysis
         └─→ needs_refinement=False → PROCEED
                  ↓
         ┌─────────────────┐
         │ risk_assessment │ - Filters unreliable signals
         └────────┬────────┘
                  ↓
                 END
```

## Node Details

### 1. Base Context Node

**Purpose**: Retrieve foundational context before planning

**LLM Used**: ❌ No

**Tools Called**:
- `get_recent_news_items(timestamp, db_manager, hours=24, limit=10)`
  - Returns: List of 10 most recent news items
  
- `find_similar_news(news_text, timestamp, db_manager, top_k=10)`
  - Returns: List of 10 similar historical news with price impact

**State Updates**:
- `recent_news`: List[Dict] - 10 recent news
- `similar_news`: List[Dict] - 10 similar news
- `messages`: Adds message about context retrieval

**Output Example**:
```python
{
    "recent_news": [
        {"id": 123, "title": "...", "published": "2024-11-01 10:00:00"},
        # ... 9 more
    ],
    "similar_news": [
        {"id": 456, "title": "...", "similarity": 0.85, "price_change_pct": 2.5},
        # ... 9 more
    ]
}
```

---

### 2. Planning Node

**Purpose**: LLM creates structured action plan

**LLM Used**: ✅ Yes - Creates customized plan

**Tools Called**: ❌ None (pure LLM reasoning)

**LLM Input**:
- News text
- Recent news context (10 items)
- Similar historical events (10 items)
- Plan template

**LLM Output** (JSON):
```json
{
  "plan_steps": [
    "Step 1: Extract tickers (expect 2-3 tickers)",
    "Step 2: Analyze sentiment for each ticker",
    "Step 3: Gather technical indicators (RSI, ATR, MA)",
    "Step 4: Check sector trends",
    "Step 5: Compare with historical patterns",
    "Step 6: Generate signals with confidence"
  ],
  "reasoning": "This news mentions multiple tech companies...",
  "priority_areas": ["sentiment", "technical", "sector"],
  "expected_tickers": 2
}
```

**State Updates**:
- `action_plan`: Dict - Full plan with reasoning
- `plan_steps`: List[str] - Ordered steps
- `messages`: Adds plan details

---

### 3. Main Analysis Node

**Purpose**: Execute analysis with flexibility to deviate from plan

**LLM Used**: ✅ Yes - Orchestrates analysis and can deviate

**Tools Called** (via LLM orchestration):

1. **Ticker Validation** (US Market Only, No OTC):
   - `validate_tickers_batch(tickers_data)`
   - Returns: List[TickerInfo] - Validated US market tickers
   - Checks: Exchange (NYSE, NASDAQ, AMEX, ARCA, BATS) and Country (USA)
   - Excludes: OTC, OTCBB, Pink Sheets

2. **Sentiment Validation** (done in node):
   - LLM generates sentiment data
   - Node validates: sentiment type (bullish/bearish/neutral)
   - Node clamps intensity to 0-100 range
   - No separate tool needed

3. **Historical Data**:
   - `get_historical_prices(ticker, timestamp, lookback_days, db_manager)`
   - Returns: DataFrame with OHLCV data

4. **Technical Analysis**:
   - `calculate_technical_indicators(ticker, price_data)`
   - Returns: Dict with RSI, ATR, MA, Bollinger Bands, support/resistance

5. **Sector Analysis** (optional):
   - `analyze_sector_and_competitors(ticker, timestamp, db_manager)`
   - Returns: Dict with sector, competitors, trends

6. **Similar News Search** (NEW - LLM-driven):
   - `find_similar_news(search_query, timestamp, db_manager, top_k=10)`
   - LLM generates search query based on analysis needs
   - Example: "Apple chip performance breakthrough" or "Microsoft AI competition"
   - Returns: List of similar news with price impact

7. **Ticker-Specific Recent News** (NEW):
   - `get_recent_news_items(timestamp, db_manager, hours=24, ticker="AAPL", limit=10)`
   - Get recent news for specific ticker during analysis
   - Returns: List of recent news for that ticker

**LLM Input**:
- Message history (full context)
- Action plan
- News text
- Recent news context
- Similar historical events

**LLM Output** (JSON):
```json
{
  "tickers": ["AAPL", "MSFT"],
  "sentiment": {
    "AAPL": {
      "sentiment": "bullish",
      "intensity": 85,
      "reasoning": "Strong product announcement"
    },
    "MSFT": {
      "sentiment": "neutral",
      "intensity": 45,
      "reasoning": "Mentioned as competitor"
    }
  },
  "key_findings": [
    "Apple announces M4 chip with 40% performance boost",
    "Microsoft mentioned as competitor"
  ],
  "deviations_from_plan": [
    "Added MSFT analysis (not in original plan but mentioned in news)",
    "Searched for similar chip announcements to gauge market reaction"
  ],
  "signals": [
    {
      "ticker": "AAPL",
      "signal": "BUY",
      "confidence": 0.75,
      "reasoning": "Strong bullish sentiment + technical confirmation"
    }
  ]
}
```

**Example: LLM-Driven Tool Usage**

The LLM can dynamically call tools based on analysis needs:

1. **Similar News with Custom Query**:
```python
# LLM decides to search for similar chip announcements
search_query = "Apple chip performance breakthrough M1 M2 M3"
similar_chip_news = find_similar_news(search_query, timestamp, db_manager, top_k=5)

# Or search for competitor reactions
search_query = "Microsoft Intel AMD chip announcement reaction"
competitor_news = find_similar_news(search_query, timestamp, db_manager, top_k=5)
```

2. **Ticker-Specific Recent News**:
```python
# LLM wants to check recent AAPL news for context
aapl_recent = get_recent_news_items(timestamp, db_manager, hours=24, ticker="AAPL", limit=10)

# Check if MSFT had recent news too
msft_recent = get_recent_news_items(timestamp, db_manager, hours=48, ticker="MSFT", limit=5)
```

**Why This is Powerful**:
- LLM can search for specific patterns: "earnings beat", "product recall", "CEO change"
- LLM can focus on relevant tickers instead of getting all news
- LLM can adjust time windows: 24h for hot stocks, 48h for slower movers
- Search queries can be contextual: "Apple vs Samsung competition" when both are mentioned

**State Updates**:
- `tickers`: List[str] - Identified tickers
- `analysis_results`: Dict - All analysis data
- `signals`: List[Dict] - Generated signals
- `plan_deviations`: List[str] - Deviations from plan
- `messages`: Adds analysis summary

**Key Feature**: ⭐ **Can deviate from plan**
- If plan says "analyze 2 tickers" but LLM finds 3 → analyzes all 3
- If plan misses important analysis → LLM adds it
- All deviations tracked in `plan_deviations`

---

### 4. Reflection Node

**Purpose**: Review analysis and decide next step

**LLM Used**: ✅ Yes - Reviews and decides

**Tools Called**: ❌ None (pure LLM reasoning)

**LLM Input**:
- Full message history
- Current tickers analyzed
- Signals generated
- Plan deviations
- Iteration count

**LLM Output** (JSON):
```json
{
  "needs_refinement": false,
  "loop_to": null,
  "advice": [
    "Analysis looks complete",
    "All tickers properly analyzed"
  ],
  "reasoning": "Sentiment, technical, and sector analysis all complete",
  "confidence_in_analysis": 0.85
}
```

**Decision Options**:

1. **SATISFIED** → Proceed to risk assessment
```json
{
  "needs_refinement": false,
  "loop_to": null
}
```

2. **NEEDS BETTER PLAN** → Loop to planning
```json
{
  "needs_refinement": true,
  "loop_to": "planning",
  "advice": ["Plan missed MSFT competitor, need to replan"]
}
```

3. **NEEDS MORE ANALYSIS** → Loop to main_analysis
```json
{
  "needs_refinement": true,
  "loop_to": "main_analysis",
  "advice": ["Missing volatility check for TSLA"]
}
```

**State Updates**:
- `needs_refinement`: bool
- `loop_to`: str ("planning" or "main_analysis")
- `reflection_advice`: List[str]
- `reflection_count`: int (incremented)
- `messages`: Adds reflection reasoning

**Safety**: Max 2 iterations (configurable via `max_iterations`)

---

### 5. Risk Assessment Node

**Purpose**: Filter unreliable signals based on risk checks

**LLM Used**: ❌ No

**Tools Called**:

1. **Pre-News Movement Detection** (NEW):
   - `check_multiple_tickers_pre_news(tickers, news_timestamp, db_manager, window_minutes=60)`
   - Returns: Dict[ticker, movement_analysis]
   - Checks: Price movement >2%, volume spikes before news

2. **Volatility Detection**:
   - `assess_risk(ticker, timestamp, db_manager, sentiment_data, technical_indicators, sector_analysis)`
   - Returns: Dict with risk_level, risk_score, is_hot_stock

**Risk Checks**:

| Check | Threshold | Flag |
|-------|-----------|------|
| Pre-news price movement | >2% in 60 min before | "Price leaked before news" |
| Pre-news volume spike | >50% vs baseline | "Unusual volume before announcement" |
| Extreme price movement | >3% before news | "EXTREME price movement" |
| Hot stock | High volatility + volume | "Stock already hot from recent events" |

**Filtering Logic**:
```python
for signal in signals:
    ticker = signal["ticker"]
    if ticker has risk_flags:
        # Filter out signal
        print(f"Filtering {ticker}: {risk_flags}")
    else:
        # Keep signal
        filtered_signals.append(signal)
```

**State Updates**:
- `risk_flags`: Dict[str, List[str]] - Flags per ticker
- `filtered_signals`: List[Dict] - Signals that passed checks
- `messages`: Adds risk assessment summary

**Output Example**:
```python
{
    "risk_flags": {
        "AAPL": [
            "Price moved +2.5% before news",
            "Volume spiked 65% before news"
        ]
    },
    "filtered_signals": [
        # Only signals without risk flags
    ]
}
```

---

## Tool Reference

### Data Retrieval Tools

| Tool | Purpose | Returns |
|------|---------|---------|
| `get_recent_news_items` | Get recent news | List[Dict] with id, title, summary, published |
| `find_similar_news` | Find similar historical news | List[Dict] with similarity, price_impact |
| `get_historical_prices` | Get OHLCV data | DataFrame with price data |

### Analysis Tools

| Tool | Purpose | Returns |
|------|---------|---------|
| `validate_tickers_batch` | Validate US market tickers | List[TickerInfo] (US only) |
| `calculate_technical_indicators` | Technical analysis | Dict with RSI, ATR, MA, BB, support/resistance |
| `analyze_sector_and_competitors` | Sector analysis | Dict with sector, competitors, trends |

**Notes**: 
- Sentiment validation is now done directly in the node, not as a separate tool.
- Ticker validation only accepts US market stocks (NYSE, NASDAQ, AMEX, ARCA, BATS)
- OTC markets (OTC, OTCBB, Pink Sheets) are excluded

### Risk Tools

| Tool | Purpose | Returns |
|------|---------|---------|
| `check_pre_news_movement` | Detect pre-news leaks | Dict with suspicious flag, price/volume changes |
| `check_multiple_tickers_pre_news` | Batch pre-news check | Dict[ticker, analysis] |
| `assess_risk` | Comprehensive risk | Dict with risk_level, is_hot_stock |
| `detect_volatility_and_volume` | Volatility detection | Dict with volatility metrics |

---

## Message Flow Example

**Using LangChain Native Message Format:**

```python
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage

messages = [
    # System initialization
    SystemMessage(content="Analyzing news: Apple announces...", 
                  additional_kwargs={"name": "init"}),
    
    # Base context
    AIMessage(content="Retrieved 10 recent news and 10 similar events", 
              additional_kwargs={"name": "base_context", "data": {"recent_count": 10, "similar_count": 10}}),
    
    # Planning
    AIMessage(content="Created action plan with 6 steps\nReasoning: ...", 
              additional_kwargs={"name": "planning", "data": {"plan": {...}}}),
    
    # Main analysis
    AIMessage(content="Analyzed 2 tickers: AAPL, MSFT\nDeviations: Added MSFT", 
              additional_kwargs={"name": "main_analysis", "data": {"tickers": ["AAPL", "MSFT"], "deviations": [...]}}),
    
    # Reflection (loop to planning)
    AIMessage(content="Needs refinement → loop to planning\nReasoning: Plan incomplete", 
              additional_kwargs={"name": "reflection", "data": {"needs_refinement": True, "loop_to": "planning"}}),
    
    # Planning (iteration 2)
    AIMessage(content="Updated plan with MSFT analysis", 
              additional_kwargs={"name": "planning", "data": {"plan": {...}}}),
    
    # Main analysis (iteration 2)
    AIMessage(content="Analyzed both tickers completely", 
              additional_kwargs={"name": "main_analysis", "data": {...}}),
    
    # Reflection (satisfied)
    AIMessage(content="Analysis complete → proceeding", 
              additional_kwargs={"name": "reflection", "data": {"needs_refinement": False}}),
    
    # Risk assessment
    AIMessage(content="Risk assessment: 2 signals → 1 passed\nFiltered AAPL due to pre-news leak", 
              additional_kwargs={"name": "risk_assessment", "data": {"filtered_signals": 1, "risk_flags": {...}}})
]
```

**Message Types:**
- `SystemMessage`: System-level notifications and initialization
- `HumanMessage`: User inputs (if any)
- `AIMessage`: Agent reasoning and decisions from nodes
- `ToolMessage`: Tool execution results (with tool_call_id)

---

## State Schema

```python
class TradingAgentState(TypedDict):
    # Input
    news_text: str
    news_id: Optional[int]
    timestamp: datetime
    
    # Message history (CORE) - LangChain native format
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Base context
    recent_news: List[Dict]  # 10 recent news
    similar_news: List[Dict]  # 10 similar news
    
    # Planning
    action_plan: Dict[str, Any]
    plan_steps: List[str]
    
    # Analysis
    tickers: List[str]
    analysis_results: Dict[str, Any]
    signals: List[Dict[str, Any]]
    plan_deviations: List[str]  # NEW
    
    # Reflection
    reflection_count: int
    reflection_advice: List[str]
    needs_refinement: bool
    loop_to: str  # "planning" or "main_analysis"
    
    # Risk
    risk_flags: Dict[str, List[str]]
    filtered_signals: List[Dict[str, Any]]  # FINAL OUTPUT
    
    # Metadata
    iteration: int
    max_iterations: int  # Default: 2
```

---

## Configuration

```python
# Agent config
MAX_REFLECTION_ITERATIONS = 2  # Max loops
LOOKBACK_DAYS_TECHNICAL = 30  # For technical analysis
RECENT_NEWS_HOURS = 24  # Recent news window
SIMILAR_NEWS_TOP_K = 10  # Similar news limit
PRE_NEWS_WINDOW_MINUTES = 60  # Pre-news leak detection window

# Risk thresholds
PRE_NEWS_PRICE_THRESHOLD = 2.0  # % price movement
PRE_NEWS_VOLUME_THRESHOLD = 50.0  # % volume increase
EXTREME_MOVEMENT_THRESHOLD = 3.0  # % extreme movement
```

---

## LLM-Driven Tool Usage Examples

### Use Case 1: Contextual Similar News Search

**Scenario**: News mentions "Apple announces M4 chip with 40% performance boost"

**LLM Reasoning**: "I should search for similar chip announcements to see historical market reactions"

**Tool Call**:
```python
search_query = "Apple chip performance breakthrough M1 M2 M3 announcement"
similar_news = find_similar_news(search_query, timestamp, db_manager, top_k=5)
```

**Result**: Finds M1, M2, M3 announcements with price impacts: +3.2%, +2.8%, +4.1%

**LLM Conclusion**: "Historical chip announcements show 3-4% average gain"

---

### Use Case 2: Ticker-Specific Recent Context

**Scenario**: Analyzing AAPL but want to check for recent news

**LLM Reasoning**: "Before generating signal, check if AAPL had other news today"

**Tool Call**:
```python
aapl_recent = get_recent_news_items(
    timestamp=timestamp,
    db_manager=db_manager,
    hours=24,
    ticker="AAPL",
    limit=10
)
```

**Result**: Finds 3 other AAPL news items in last 24h

**LLM Conclusion**: "AAPL already had 3 news items today, stock may be overheated"

---

### Use Case 3: Competitor Analysis

**Scenario**: News mentions both Apple and Microsoft

**LLM Reasoning**: "Should check recent MSFT news to understand competitive context"

**Tool Calls**:
```python
# Get AAPL recent news
aapl_news = get_recent_news_items(timestamp, db_manager, hours=48, ticker="AAPL", limit=5)

# Get MSFT recent news
msft_news = get_recent_news_items(timestamp, db_manager, hours=48, ticker="MSFT", limit=5)

# Search for competitive news
search_query = "Apple Microsoft competition AI chip technology"
competitive_news = find_similar_news(search_query, timestamp, db_manager, top_k=5)
```

**Result**: Comprehensive competitive landscape

---

### Use Case 4: Event-Specific Search

**Scenario**: News about earnings beat

**LLM Reasoning**: "Search for similar earnings beats to gauge typical market reaction"

**Tool Call**:
```python
search_query = "Apple earnings beat revenue surprise positive"
earnings_history = find_similar_news(search_query, timestamp, db_manager, top_k=10)
```

**Result**: Historical earnings beats show 2-5% gains typically

---

### Use Case 5: Sector-Wide Event

**Scenario**: News about chip shortage affecting multiple companies

**LLM Reasoning**: "Check recent news for all affected tickers"

**Tool Calls**:
```python
for ticker in ["AAPL", "MSFT", "NVDA", "AMD"]:
    ticker_news = get_recent_news_items(
        timestamp, db_manager, hours=72, ticker=ticker, limit=5
    )
    # Analyze each ticker's recent news context
```

**Result**: Comprehensive sector analysis

---

## Key Features Summary

1. ✅ **Message-based state** - Full conversation history
2. ✅ **Planning node** - LLM creates customized plan
3. ✅ **Flexible main node** - Can deviate from plan
4. ✅ **LLM-driven tool usage** - Dynamic search queries and ticker filtering
5. ✅ **3-way reflection** - Loop to planning OR main OR proceed
6. ✅ **Pre-news leak detection** - Filters suspicious signals
7. ✅ **Risk filtering** - Returns only reliable signals
8. ✅ **Iterative improvement** - Max 2 reflection loops
9. ✅ **Context accumulation** - Each node sees full history

