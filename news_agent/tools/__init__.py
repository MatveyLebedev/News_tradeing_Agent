"""Trading agent tools - refactored for pure data operations"""

# Ticker validation tools (no LLM)
from .ticker_extraction_tool import validate_ticker, validate_tickers_batch

# Sentiment structure tools (no LLM)
from .sentiment_analysis_tool import get_sentiment_template

# Database query tools (no LLM)
from .db_query_tool import get_historical_prices, get_price_at_time

# Technical analysis tools (no LLM)
from .technical_analysis_tool import calculate_technical_indicators

# Sector and competitor tools (no LLM)
from .sector_competitor_tool import analyze_sector_and_competitors

# Historical pattern tools (no LLM)
from .historical_pattern_tool import find_similar_historical_news

# Risk assessment tools (no LLM)
from .risk_assessment_tool import assess_risk

# Volatility detection tools (no LLM)
from .volatility_volume_detector_tool import detect_volatility_and_volume

# News retrieval tools (no LLM)
from .get_news_by_id import get_news_with_price_impact
from .get_recent_news import get_recent_news_items

# Pre-news movement detection (NEW)
from .pre_news_movement_tool import check_pre_news_movement, check_multiple_tickers_pre_news, create_get_pre_news_movement_tool

# Similar news finder (NEW)
from .find_similar_news_tool import find_similar_news, find_similar_news_for_ticker, create_find_similar_news_tool, create_find_similar_news_with_price_tool

# Ticker analysis tool (NEW)
from .ticker_analysis_tool import analyze_ticker_with_similar_news, get_ticker_historical_context, create_ticker_analysis_tool, create_ticker_historical_context_tool

# LLM-callable tool creators
from .get_recent_news import create_get_recent_news_tool
from .db_query_tool import create_get_price_data_tool, create_get_price_at_time_tool
from .technical_analysis_tool import create_get_technical_indicators_tool
from .sector_competitor_tool import create_analyze_sector_tool
from .risk_assessment_tool import create_check_risk_tool
from .ticker_extraction_tool import create_validate_ticker_tool

# Graph (Neo4j) tools
from .graph_tools import create_graph_tools

__all__ = [
    # Ticker tools
    'validate_ticker',
    'validate_tickers_batch',
    
    # Sentiment tools
    'get_sentiment_template',
    
    # Database tools
    'get_historical_prices',
    'get_price_at_time',
    
    # Technical analysis
    'calculate_technical_indicators',
    
    # Sector analysis
    'analyze_sector_and_competitors',
    
    # Pattern matching
    'find_similar_historical_news',
    
    # Risk assessment
    'assess_risk',
    'detect_volatility_and_volume',
    
    # News retrieval
    'get_news_with_price_impact',
    'get_recent_news_items',
    
    # Pre-news movement detection
    'check_pre_news_movement',
    'check_multiple_tickers_pre_news',
    'create_get_pre_news_movement_tool',
    
    # Similar news finder
    'find_similar_news',
    'find_similar_news_for_ticker',
    
    # Ticker analysis
    'analyze_ticker_with_similar_news',
    'get_ticker_historical_context',
    
    # LLM tool creators
    'create_find_similar_news_tool',
    'create_find_similar_news_with_price_tool',
    'create_ticker_analysis_tool',
    'create_ticker_historical_context_tool',
    'create_get_recent_news_tool',
    'create_get_price_data_tool',
    'create_get_price_at_time_tool',
    'create_get_technical_indicators_tool',
    'create_analyze_sector_tool',
    'create_check_risk_tool',
    'create_validate_ticker_tool',

    # Graph tools
    'create_graph_tools',
]
