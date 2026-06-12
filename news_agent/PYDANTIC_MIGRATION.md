# Pydantic Migration Guide

## Overview

All tools and nodes now use **Pydantic models** for structured outputs. This provides:
- ✅ **Type safety** with automatic validation
- ✅ **Clear contracts** between components
- ✅ **Auto-generated documentation** from schemas
- ✅ **Runtime validation** of all data
- ✅ **JSON serialization** support

## Core Schemas File

All Pydantic models are defined in `/news_agent/schemas.py`:

```python
from news_agent.schemas import (
    # Tool outputs
    TickerExtractionOutput, TickerInfo,
    SentimentAnalysisOutput, SentimentAnalysis,
    TechnicalIndicators,
    SectorAnalysisOutput, CompetitorInfo,
    HistoricalPatternsOutput, HistoricalNewsPattern,
    VolatilityVolumeOutput,
    RiskAssessmentOutput, RiskComponent,
    RecentNewsOutput, RecentNewsItem,
    
    # Node outputs
    TickerExtractionNodeOutput,
    SentimentAnalysisNodeOutput,
    SignalGenerationNodeOutput,
    ConfidenceEvaluationNodeOutput,
    
    # Final output
    FinalTradingSignal,
    AgentOutput,
    
    # Enums
    SentimentType, SignalType, RiskLevel, TrendType
)
```

## Migration Pattern

### Before (Dict-based):
```python
@tool
def my_tool(input: str) -> Dict[str, Any]:
    return {
        "value": 42,
        "status": "ok"
    }
```

### After (Pydantic-based):
```python
from ..schemas import MyToolOutput

@tool
def my_tool(input: str) -> MyToolOutput:
    return MyToolOutput(
        value=42,
        status="ok"
    )
```

## Tool Migration Checklist

### ✅ Already Migrated:
1. **ticker_extraction_tool.py** → Returns `TickerExtractionOutput`
2. **sentiment_analysis_tool.py** → Returns `SentimentAnalysisOutput`

### 🔄 To Migrate:

#### 3. **technical_analysis_tool.py**
```python
from ..schemas import TechnicalIndicators, TrendType

@tool
def calculate_technical_indicators(ticker: str, ...) -> TechnicalIndicators:
    # Calculate indicators...
    return TechnicalIndicators(
        ticker=ticker,
        current_price=150.25,
        atr=2.5,
        rsi=65.2,
        ma_short=148.5,
        ma_long=145.0,
        bollinger_upper=155.0,
        bollinger_lower=145.0,
        resistance=152.0,
        support=147.0,
        distance_to_resistance_pct=1.2,
        distance_to_support_pct=-2.1,
        trend=TrendType.BULLISH
    )
```

#### 4. **sector_competitor_tool.py**
```python
from ..schemas import SectorAnalysisOutput, CompetitorInfo, TrendType

@tool
def analyze_sector(ticker: str, ...) -> SectorAnalysisOutput:
    competitors = [
        CompetitorInfo(
            ticker="MSFT",
            current_price=380.50,
            price_change_5d_pct=2.3
        ),
        # ...
    ]
    
    return SectorAnalysisOutput(
        ticker=ticker,
        sector="Technology",
        industry="Software",
        competitors=competitors,
        sector_trend=TrendType.BULLISH,
        sector_avg_change_5d=1.8
    )
```

#### 5. **historical_pattern_tool.py**
```python
from ..schemas import HistoricalPatternsOutput, HistoricalNewsPattern

@tool
def find_similar_historical_news(news_text: str, ...) -> HistoricalPatternsOutput:
    patterns = [
        HistoricalNewsPattern(
            id=123,
            title="Apple announces M3 chip",
            summary="...",
            published=datetime(2024, 10, 15, 14, 30),
            similarity=0.85,
            price_change_pct=3.2,
            price_change_abs=4.75,
            direction="up"
        ),
        # ...
    ]
    
    return HistoricalPatternsOutput(
        ticker=ticker,
        patterns=patterns,
        average_impact_pct=None  # Auto-calculated by validator
    )
```

#### 6. **volatility_volume_detector_tool.py**
```python
from ..schemas import VolatilityVolumeOutput

@tool
def detect_volatility_volume(ticker: str, ...) -> VolatilityVolumeOutput:
    return VolatilityVolumeOutput(
        ticker=ticker,
        volatility_detected=True,
        volume_spike_detected=True,
        recent_volatility_pct=4.5,
        avg_volatility_pct=2.1,
        volatility_ratio=2.14,
        recent_avg_volume=50000000,
        historical_avg_volume=25000000,
        volume_ratio=2.0,
        volatility_score=85,
        likely_cause="Previous earnings report exceeded expectations",
        is_hot_stock=True
    )
```

#### 7. **risk_assessment_tool.py**
```python
from ..schemas import RiskAssessmentOutput, RiskComponent, RiskLevel

@tool
def assess_risk(ticker: str, volatility_data: VolatilityVolumeOutput, ...) -> RiskAssessmentOutput:
    components = RiskComponent(
        volatility_risk=20.0,
        volume_risk=15.0,
        technical_risk=10.0,
        sentiment_risk=5.0,
        sector_risk=3.0
    )
    
    return RiskAssessmentOutput(
        ticker=ticker,
        risk_score=53,
        risk_level=RiskLevel.MEDIUM,
        risk_components=components,
        risk_factors=["High recent volatility", "Volume spike detected"],
        is_hot_stock=True,
        recommendation="Use wider stop-loss due to increased volatility",
        volatility_data=volatility_data
    )
```

#### 8. **get_recent_news.py**
```python
from ..schemas import RecentNewsOutput, RecentNewsItem

@tool
def get_recent_news(hours: int = 24, ticker: Optional[str] = None, ...) -> RecentNewsOutput:
    news_items = [
        RecentNewsItem(
            id=456,
            title="Apple announces new product",
            summary="Apple revealed...",
            published=datetime(2025, 11, 6, 10, 30)
        ),
        # ...
    ]
    
    return RecentNewsOutput(
        ticker=ticker,
        news_items=news_items,
        total_count=len(news_items),  # Auto-validated
        hours_lookback=hours
    )
```

#### 9. **get_news_by_id.py**
Already returns dict with price impact - keep as is or create `NewsWithImpactOutput` schema if needed.

## Node Migration

Nodes should use structured outputs in their state updates:

### Example: ticker_extraction_node

```python
from ..schemas import TickerExtractionNodeOutput

def ticker_extraction_node(state: TradingAgentState) -> dict:
    """Extract tickers from news text"""
    
    # Call tool
    result = extract_tickers(state["news_text"], state["llm"])
    
    # Extract ticker strings
    tickers = [t.ticker for t in result.tickers]
    
    # Log reasoning
    reasoning = f"Extracted {result.total_found} ticker(s): {', '.join(tickers)}"
    
    # Return structured output
    return {
        "tickers": tickers,
        "ticker_info": result.tickers,  # Keep full TickerInfo objects
        "reasoning_steps": state["reasoning_steps"] + [reasoning]
    }
```

### Example: sentiment_analysis_node

```python
def sentiment_analysis_node(state: TradingAgentState) -> dict:
    """Analyze sentiment for all tickers"""
    
    result = analyze_sentiment(
        state["news_text"],
        state["tickers"],
        state["llm"]
    )
    
    reasoning = f"Analyzed sentiment for {len(result.sentiments)} ticker(s)"
    
    return {
        "sentiment": result.sentiments,  # Dict[str, SentimentAnalysis]
        "reasoning_steps": state["reasoning_steps"] + [reasoning]
    }
```

### Example: signal_generation_node

```python
from ..schemas import SignalOutput, SignalType

def signal_generation_node(state: TradingAgentState) -> dict:
    """Generate trading signal with LLM"""
    
    # ... LLM call ...
    
    signal_output = SignalOutput(
        signal=SignalType.BUY,
        stop_loss=148.50,
        take_profit=165.00,
        reasoning="Strong bullish sentiment (85/100) combined with..."
    )
    
    # Store in ticker_results
    ticker_results = state.get("ticker_results", {})
    ticker_results[ticker]["signal"] = signal_output
    
    return {
        "ticker_results": ticker_results,
        "reasoning_steps": state["reasoning_steps"] + [
            f"{ticker}: Generated {signal_output.signal} signal"
        ]
    }
```

## State Schema Updates

Update `agent/state.py` to use Pydantic types:

```python
from typing import TypedDict, List, Dict, Optional
from datetime import datetime
import pandas as pd
from ..schemas import (
    TickerInfo,
    SentimentAnalysis,
    TechnicalIndicators,
    SectorAnalysisOutput,
    HistoricalPatternsOutput,
    RiskAssessmentOutput,
    SignalOutput,
    FinalTradingSignal
)

class TradingAgentState(TypedDict):
    # Input
    news_text: str
    timestamp: datetime
    
    # Ticker extraction
    tickers: List[str]  # Simple list of ticker strings
    ticker_info: List[TickerInfo]  # Full Pydantic models
    
    # Current iteration
    current_ticker: Optional[str]
    ticker_results: Dict[str, Dict]  # Results per ticker
    
    # Analysis results (Pydantic models)
    sentiment: Dict[str, SentimentAnalysis]
    historical_prices: Dict[str, pd.DataFrame]
    technical_indicators: Dict[str, TechnicalIndicators]
    sector_analysis: Dict[str, SectorAnalysisOutput]
    historical_patterns: Dict[str, HistoricalPatternsOutput]
    risk_assessment: Dict[str, RiskAssessmentOutput]
    
    # Signals
    signals: List[FinalTradingSignal]  # Pydantic models
    
    # Process tracking
    reasoning_steps: List[str]
    
    # Dependencies
    llm: Any
    db_manager: Any
    config: Any
```

## Final Output

The agent's `run()` method should return `AgentOutput`:

```python
from news_agent.schemas import AgentOutput, FinalTradingSignal

class TradingAgent:
    def run(self, news_text: str, timestamp: datetime) -> AgentOutput:
        start_time = time.time()
        
        # ... run graph ...
        
        final_state = graph.invoke(initial_state, config)
        
        # Extract signals (already FinalTradingSignal objects)
        signals = final_state.get("signals", [])
        
        # Return structured output
        return AgentOutput(
            signals=signals,
            total_tickers_analyzed=len(final_state.get("tickers", [])),
            signals_generated=len(signals),
            execution_time_seconds=time.time() - start_time,
            timestamp=timestamp
        )
```

## Benefits

### 1. Type Safety
```python
# Before: runtime errors
result["intensity"] = 150  # Oops, should be 0-100

# After: validation error caught immediately
SentimentAnalysis(
    ticker="AAPL",
    sentiment=SentimentType.BULLISH,
    intensity=150  # ❌ ValidationError: ensure this value is less than or equal to 100
)
```

### 2. IDE Support
```python
# Full autocomplete and type hints
result: SentimentAnalysisOutput = analyze_sentiment(...)
result.sentiments["AAPL"].  # <-- IDE shows: sentiment, intensity, reasoning, key_phrases
```

### 3. Documentation
```python
# Pydantic models self-document
>>> SentimentAnalysis.schema()
{
  "title": "SentimentAnalysis",
  "type": "object",
  "properties": {
    "ticker": {"type": "string"},
    "sentiment": {"enum": ["bullish", "bearish", "neutral"]},
    "intensity": {"type": "integer", "minimum": 0, "maximum": 100},
    ...
  }
}
```

### 4. JSON Serialization
```python
# Easy export to JSON
signal = FinalTradingSignal(...)
json_str = signal.model_dump_json()  # Convert to JSON

# Easy import from JSON
loaded = FinalTradingSignal.model_validate_json(json_str)
```

## Testing

Update tests to use Pydantic models:

```python
def test_ticker_extraction():
    result = extract_tickers("Apple announces...", llm)
    
    # Type checking
    assert isinstance(result, TickerExtractionOutput)
    assert isinstance(result.tickers[0], TickerInfo)
    
    # Validation
    assert result.total_found == len(result.tickers)
    assert 0 <= result.tickers[0].relevance <= 1
```

## Common Patterns

### Accessing nested data
```python
# Before
sentiment_str = state["sentiment"]["AAPL"]["sentiment"]

# After
sentiment_analysis: SentimentAnalysis = state["sentiment"]["AAPL"]
sentiment_str = sentiment_analysis.sentiment.value  # SentimentType enum
```

### Error handling
```python
from pydantic import ValidationError

try:
    result = TechnicalIndicators(**raw_data)
except ValidationError as e:
    print(f"Validation error: {e}")
    # Handle invalid data
```

### Default values
```python
# Schemas include sensible defaults
output = TickerExtractionOutput(
    tickers=[],  # Empty list is valid
    total_found=0  # Validator will sync with tickers length
)
```

## Migration Priority

1. ✅ **schemas.py** - Define all models
2. ✅ **ticker_extraction_tool.py** - Completed
3. ✅ **sentiment_analysis_tool.py** - Completed
4. 🔄 **technical_analysis_tool.py**
5. 🔄 **sector_competitor_tool.py**
6. 🔄 **historical_pattern_tool.py**
7. 🔄 **volatility_volume_detector_tool.py**
8. 🔄 **risk_assessment_tool.py**
9. 🔄 **get_recent_news.py**
10. 🔄 **All nodes in agent/nodes.py**
11. 🔄 **trading_agent.py** main entry point
12. 🔄 **Update tests**

## Next Steps

1. Migrate remaining tools following the pattern above
2. Update all nodes to work with Pydantic models
3. Update `trading_agent.py` to return `AgentOutput`
4. Update tests to validate Pydantic schemas
5. Update documentation with new type signatures

