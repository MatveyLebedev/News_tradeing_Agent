"""
Pydantic schemas for structured outputs in trading agent
All tools and nodes use these schemas for type safety and validation
"""
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Literal, Any
from datetime import datetime
from enum import Enum


# ============================================================================
# Enums
# ============================================================================

class SentimentType(str, Enum):
    """Sentiment classification"""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalType(str, Enum):
    """Trade signal types"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class RiskLevel(str, Enum):
    """Risk level classification"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TrendType(str, Enum):
    """Market trend classification"""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# ============================================================================
# Tool Output Schemas
# ============================================================================

class TickerInfo(BaseModel):
    """Single ticker extraction result"""
    ticker: str = Field(..., description="Stock ticker symbol (e.g., AAPL)")
    relevance: float = Field(..., ge=0, le=1, description="Relevance score 0-1")
    reason: str = Field(..., description="Why this ticker was identified")
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticker": "AAPL",
                "relevance": 0.95,
                "reason": "directly mentioned in article"
            }
        }


class TickerExtractionOutput(BaseModel):
    """Output from ticker extraction tool"""
    tickers: List[TickerInfo] = Field(default_factory=list, description="List of identified tickers")
    total_found: int = Field(..., description="Total number of tickers found")
    
    @validator('total_found', always=True)
    def validate_total(cls, v, values):
        """Ensure total_found matches tickers list length"""
        if 'tickers' in values:
            return len(values['tickers'])
        return v


class SentimentAnalysis(BaseModel):
    """Sentiment analysis for a single ticker"""
    ticker: str = Field(..., description="Stock ticker symbol")
    sentiment: SentimentType = Field(..., description="Overall sentiment")
    intensity: int = Field(..., ge=0, le=100, description="Sentiment intensity 0-100")
    reasoning: str = Field(..., description="Explanation of sentiment")
    key_phrases: List[str] = Field(default_factory=list, description="Supporting phrases from news")
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticker": "AAPL",
                "sentiment": "bullish",
                "intensity": 85,
                "reasoning": "Major product breakthrough announced",
                "key_phrases": ["breakthrough M4 chip", "revolutionary performance"]
            }
        }


class SentimentAnalysisOutput(BaseModel):
    """Output from sentiment analysis tool"""
    sentiments: Dict[str, SentimentAnalysis] = Field(..., description="Sentiment per ticker")


class TechnicalIndicators(BaseModel):
    """Technical analysis indicators"""
    ticker: str = Field(..., description="Stock ticker symbol")
    current_price: float = Field(..., gt=0, description="Current/latest price")
    atr: float = Field(..., ge=0, description="Average True Range")
    rsi: float = Field(..., ge=0, le=100, description="Relative Strength Index")
    ma_short: float = Field(..., gt=0, description="Short-term moving average")
    ma_long: float = Field(..., gt=0, description="Long-term moving average")
    bollinger_upper: float = Field(..., gt=0, description="Upper Bollinger Band")
    bollinger_lower: float = Field(..., gt=0, description="Lower Bollinger Band")
    resistance: float = Field(..., gt=0, description="Resistance level")
    support: float = Field(..., gt=0, description="Support level")
    distance_to_resistance_pct: float = Field(..., description="% distance to resistance")
    distance_to_support_pct: float = Field(..., description="% distance to support")
    trend: TrendType = Field(..., description="Overall trend direction")
    
    @validator('support', 'ma_long', 'ma_short', 'bollinger_lower', 'bollinger_upper', 'resistance')
    def validate_price_levels(cls, v, values):
        """Validate logical price levels"""
        if 'current_price' in values and v > 0:
            # Just ensure it's positive
            return v
        return v


class CompetitorInfo(BaseModel):
    """Information about a competitor"""
    ticker: str = Field(..., description="Competitor ticker")
    current_price: float = Field(..., gt=0, description="Current price")
    price_change_5d_pct: float = Field(..., description="5-day price change %")


class SectorAnalysisOutput(BaseModel):
    """Output from sector analysis tool"""
    ticker: str = Field(..., description="Primary ticker being analyzed")
    sector: str = Field(..., description="Sector name")
    industry: str = Field(..., description="Industry name")
    competitors: List[CompetitorInfo] = Field(default_factory=list, description="Competitor data")
    sector_trend: TrendType = Field(..., description="Overall sector trend")
    sector_avg_change_5d: float = Field(..., description="Average 5-day sector change %")


class HistoricalNewsPattern(BaseModel):
    """Historical news with price impact"""
    id: int = Field(..., description="Database ID of news")
    title: str = Field(..., description="News title")
    summary: str = Field(..., description="News summary")
    published: datetime = Field(..., description="Publication timestamp")
    similarity: float = Field(..., ge=0, le=1, description="Similarity score to current news")
    price_change_pct: float = Field(..., description="Price change % 40min after")
    price_change_abs: float = Field(..., description="Absolute price change")
    direction: Literal["up", "down", "flat"] = Field(..., description="Price direction")


class HistoricalPatternsOutput(BaseModel):
    """Output from historical pattern matching tool"""
    ticker: str = Field(..., description="Ticker analyzed")
    patterns: List[HistoricalNewsPattern] = Field(default_factory=list, description="Similar historical events")
    average_impact_pct: Optional[float] = Field(None, description="Average price impact %")
    
    @validator('average_impact_pct', always=True)
    def calculate_average(cls, v, values):
        """Calculate average impact from patterns"""
        if 'patterns' in values and values['patterns']:
            return sum(p.price_change_pct for p in values['patterns']) / len(values['patterns'])
        return None


class VolatilityVolumeOutput(BaseModel):
    """Output from volatility/volume detector tool"""
    ticker: str = Field(..., description="Ticker analyzed")
    volatility_detected: bool = Field(..., description="High volatility detected")
    volume_spike_detected: bool = Field(..., description="Volume spike detected")
    recent_volatility_pct: float = Field(..., ge=0, description="Recent volatility %")
    avg_volatility_pct: float = Field(..., ge=0, description="Average volatility %")
    volatility_ratio: float = Field(..., ge=0, description="Recent/average volatility ratio")
    recent_avg_volume: int = Field(..., ge=0, description="Recent average volume")
    historical_avg_volume: int = Field(..., ge=0, description="Historical average volume")
    volume_ratio: float = Field(..., ge=0, description="Recent/historical volume ratio")
    volatility_score: int = Field(..., ge=0, le=100, description="Volatility score 0-100")
    likely_cause: str = Field(..., description="Likely cause of volatility")
    is_hot_stock: bool = Field(..., description="Stock is 'hot' (high vol + high volume)")


class RiskComponent(BaseModel):
    """Individual risk component"""
    volatility_risk: float = Field(..., ge=0, le=30, description="Volatility risk score")
    volume_risk: float = Field(..., ge=0, le=20, description="Volume risk score")
    technical_risk: float = Field(..., ge=0, le=25, description="Technical risk score")
    sentiment_risk: float = Field(..., ge=0, le=15, description="Sentiment risk score")
    sector_risk: float = Field(..., ge=0, le=10, description="Sector risk score")


class RiskAssessmentOutput(BaseModel):
    """Output from risk assessment tool"""
    ticker: str = Field(..., description="Ticker analyzed")
    risk_score: int = Field(..., ge=0, le=100, description="Total risk score 0-100")
    risk_level: RiskLevel = Field(..., description="Risk level classification")
    risk_components: RiskComponent = Field(..., description="Breakdown of risk factors")
    risk_factors: List[str] = Field(default_factory=list, description="Identified risk factors")
    is_hot_stock: bool = Field(..., description="Stock already hot from previous news")
    recommendation: str = Field(..., description="Risk-based recommendation")
    volatility_data: VolatilityVolumeOutput = Field(..., description="Detailed volatility analysis")


class RecentNewsItem(BaseModel):
    """Recent news item"""
    id: int = Field(..., description="Database ID")
    title: str = Field(..., description="News title")
    summary: str = Field(..., description="News summary")
    published: datetime = Field(..., description="Publication timestamp")


class RecentNewsOutput(BaseModel):
    """Output from get recent news tool"""
    ticker: Optional[str] = Field(None, description="Ticker filter (if used)")
    news_items: List[RecentNewsItem] = Field(default_factory=list, description="Recent news")
    total_count: int = Field(..., description="Total items found")
    hours_lookback: int = Field(..., description="Hours searched")
    
    @validator('total_count', always=True)
    def validate_count(cls, v, values):
        """Ensure count matches items"""
        if 'news_items' in values:
            return len(values['news_items'])
        return v


# ============================================================================
# Node Output Schemas
# ============================================================================

class PlanningOutput(BaseModel):
    """Output from planning node"""
    plan_steps: List[str] = Field(..., description="Ordered list of analysis steps")
    reasoning: str = Field(..., description="Why this plan is appropriate")
    priority_areas: List[str] = Field(default_factory=list, description="Priority focus areas")
    expected_tickers: int = Field(..., description="Expected number of tickers")


class TradingSignal(BaseModel):
    """Trading signal in required format"""
    ticker: str = Field(..., description="Stock ticker symbol (e.g., 'AMD')")
    signal: Literal["buy", "sell"] = Field(..., description="Trade signal: 'buy' or 'sell'")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score 0-1")
    reasoning: str = Field(..., description="Reasoning explaining news positive/negative effect")
    novelty_score: Optional[float] = Field(
        None, ge=0, le=1,
        description="Novelty score (0-1) from get_novelty_score for this ticker/topic. "
                    "Low (<0.3) = already priced in. Copy the value you obtained from the tool."
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticker": "AMD",
                "signal": "buy",
                "confidence": 0.85,
                "reasoning": "News positive effect: Major chip breakthrough announced, strong market sentiment",
                "novelty_score": 0.82
            }
        }


class MainAnalysisOutput(BaseModel):
    """Output from main analysis node"""
    tickers: List[str] = Field(default_factory=list, description="Tickers analyzed")
    sentiment: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Sentiment per ticker")
    key_findings: List[str] = Field(default_factory=list, description="Key analysis findings")
    deviations_from_plan: List[str] = Field(default_factory=list, description="How analysis deviated from plan")
    signals: List[TradingSignal] = Field(default_factory=list, description="Generated trading signals in format: {'ticker': 'AMD', 'signal': 'buy'/'sell', 'confidence': 0.0-1.0, 'reasoning': 'news positive/negative effect', 'novelty_score': 0.0-1.0 from get_novelty_score}")


class ReflectionOutput(BaseModel):
    """Output from reflection node"""
    needs_refinement: bool = Field(..., description="Whether analysis needs refinement")
    loop_to: Optional[Literal["planning", "main_analysis"]] = Field(None, description="Which node to loop back to")
    advice: List[str] = Field(default_factory=list, description="Advice for improvement")
    reasoning: str = Field(..., description="Why this decision was made")
    confidence_in_analysis: float = Field(..., ge=0, le=1, description="Confidence in current analysis")


class TickerExtractionNodeOutput(BaseModel):
    """Output from ticker extraction node"""
    tickers: List[str] = Field(default_factory=list, description="Validated ticker symbols")
    ticker_info: List[TickerInfo] = Field(default_factory=list, description="Detailed ticker info")
    reasoning: str = Field(..., description="Reasoning step logged")


class SentimentAnalysisNodeOutput(BaseModel):
    """Output from sentiment analysis node"""
    sentiment_by_ticker: Dict[str, SentimentAnalysis] = Field(..., description="Sentiment per ticker")
    reasoning: str = Field(..., description="Reasoning step logged")


class DataRetrievalNodeOutput(BaseModel):
    """Output from data retrieval node"""
    tickers_with_data: List[str] = Field(..., description="Tickers with available data")
    tickers_without_data: List[str] = Field(..., description="Tickers without data")
    reasoning: str = Field(..., description="Reasoning step logged")


class TechnicalAnalysisNodeOutput(BaseModel):
    """Output from technical analysis node"""
    indicators: TechnicalIndicators = Field(..., description="Technical indicators")
    reasoning: str = Field(..., description="Reasoning step logged")


class SignalOutput(BaseModel):
    """Generated trade signal"""
    signal: SignalType = Field(..., description="Trade signal")
    stop_loss: float = Field(..., gt=0, description="Stop loss price")
    take_profit: float = Field(..., gt=0, description="Take profit price")
    reasoning: str = Field(..., description="Detailed reasoning for signal")
    
    @validator('take_profit')
    def validate_take_profit(cls, v, values):
        """Ensure take_profit > stop_loss for BUY signals"""
        if 'stop_loss' in values and 'signal' in values:
            if values['signal'] == SignalType.BUY and v <= values['stop_loss']:
                raise ValueError("take_profit must be > stop_loss for BUY signal")
        return v


class SignalGenerationNodeOutput(BaseModel):
    """Output from signal generation node"""
    ticker: str = Field(..., description="Ticker analyzed")
    signal_output: SignalOutput = Field(..., description="Generated signal")
    reasoning: str = Field(..., description="Reasoning step logged")


class ConfidenceScore(BaseModel):
    """Confidence score with breakdown"""
    total_confidence: float = Field(..., ge=0, le=1, description="Total confidence 0-1")
    sentiment_factor: float = Field(..., ge=0, le=0.25, description="Sentiment contribution")
    pattern_factor: float = Field(..., ge=0, le=0.25, description="Pattern contribution")
    technical_factor: float = Field(..., ge=0, le=0.25, description="Technical contribution")
    risk_factor: float = Field(..., ge=0, le=0.25, description="Risk contribution")
    hot_stock_penalty: float = Field(..., ge=0, le=1, description="Penalty multiplier if hot")


class ConfidenceEvaluationNodeOutput(BaseModel):
    """Output from confidence evaluation node"""
    ticker: str = Field(..., description="Ticker analyzed")
    confidence: float = Field(..., ge=0, le=1, description="Final confidence score")
    confidence_breakdown: ConfidenceScore = Field(..., description="Confidence breakdown")
    meets_threshold: bool = Field(..., description="Meets confidence threshold")
    threshold: float = Field(..., description="Threshold used")
    reasoning: str = Field(..., description="Reasoning step logged")


class FinalTradingSignal(BaseModel):
    """Final trading signal output"""
    ticker: str = Field(..., description="Stock ticker symbol")
    signal: SignalType = Field(..., description="Trade signal")
    stop_loss: float = Field(..., gt=0, description="Stop loss price")
    take_profit: float = Field(..., gt=0, description="Take profit price")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score")
    reasoning: str = Field(..., description="Detailed reasoning")
    
    # Additional metadata
    sentiment: Optional[SentimentType] = Field(None, description="News sentiment")
    risk_level: Optional[RiskLevel] = Field(None, description="Risk level")
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticker": "AAPL",
                "signal": "BUY",
                "stop_loss": 150.25,
                "take_profit": 165.50,
                "confidence": 0.85,
                "reasoning": "Strong positive sentiment (85/100) on breakthrough announcement...",
                "sentiment": "bullish",
                "risk_level": "low"
            }
        }


class AgentOutput(BaseModel):
    """Final output from trading agent"""
    signals: List[FinalTradingSignal] = Field(default_factory=list, description="Generated trading signals")
    total_tickers_analyzed: int = Field(..., description="Total tickers analyzed")
    signals_generated: int = Field(..., description="Signals generated")
    execution_time_seconds: Optional[float] = Field(None, description="Total execution time")
    timestamp: datetime = Field(..., description="Analysis timestamp")
    
    @validator('signals_generated', always=True)
    def validate_signals_count(cls, v, values):
        """Ensure count matches signals list"""
        if 'signals' in values:
            return len(values['signals'])
        return v


# ============================================================================
# Validation Helper Functions
# ============================================================================

def validate_ticker_format(ticker: str) -> bool:
    """Validate ticker symbol format"""
    return ticker.isupper() and ticker.isalpha() and 1 <= len(ticker) <= 5


def validate_price_levels(stop_loss: float, take_profit: float, current_price: float, signal: SignalType) -> bool:
    """Validate price levels make sense for the signal type"""
    if signal == SignalType.BUY:
        return stop_loss < current_price < take_profit
    elif signal == SignalType.SELL:
        return take_profit < current_price < stop_loss
    return True


# ============================================================================
# Significance Classifier Schemas
# ============================================================================

class SimilarNewsTickerExtraction(BaseModel):
    """Per-headline primary US ticker extraction (LLM call A in significance_check_node)"""
    tickers: Dict[int, Optional[str]] = Field(
        ...,
        description="Mapping news_id -> primary US ticker symbol (uppercase) or None if no clear public-market ticker"
    )


class PrimaryTickerExtraction(BaseModel):
    """Primary US ticker of the CURRENT news (significance_check_node).

    Mirrors the LLM-based ticker extraction used for graph DB writes
    (graph_db.event_extractor.ExtractedEvent.tickers).
    """
    ticker: Optional[str] = Field(
        None,
        description="Primary affected US stock ticker (uppercase, e.g. 'AMD'). None for macro/geopolitical/foreign-only news with no clear US-listed ticker."
    )


class SignificanceOutput(BaseModel):
    """Final classifier decision (LLM call B in significance_check_node)"""
    is_significant: bool = Field(..., description="True if news is a market-moving event for US stocks")
    confidence: float = Field(..., ge=0, le=1, description="Confidence in the classification 0-1")
    category: str = Field(
        ...,
        description="Category: earnings, M&A, product, regulatory, geopolitical, macro, executive, legal, opinion, other"
    )
    historical_reaction_summary: str = Field(
        ...,
        description="Brief summary of how similar past news affected prices (or 'no data' if none available)"
    )
    reasoning: str = Field(..., description="Why this classification was chosen")

