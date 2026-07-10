"""
Tool for comprehensive ticker analysis with similar news and price impact
Provides historical patterns and recent context for a specific ticker
"""
from typing import Dict, Any
from datetime import datetime
from langchain_core.tools import tool
import pytz
import statistics


def analyze_ticker_with_similar_news(
    ticker: str,
    news_text: str,
    timestamp: datetime,
    db_manager: Any,
    top_k: int = 10,
    window_minutes: int = 40
) -> Dict[str, Any]:
    """
    Comprehensive ticker analysis with similar news and price patterns
    
    Args:
        ticker: Stock ticker symbol
        news_text: Current news text to find similar patterns
        timestamp: Current timestamp (for data leakage prevention)
        db_manager: Database manager instance
        top_k: Number of similar news to analyze (default: 10)
        window_minutes: Time window for price impact (default: 40)
        
    Returns:
        Dict with:
            - ticker: Ticker symbol
            - similar_news_count: Number of similar events found
            - similar_news: List of similar news with price impacts
            - most_recent_similar: Most recent similar news item
            - recent_news: Recent news for this ticker (last 24h)
            - price_statistics: Statistical analysis of price impacts
            - pattern_summary: Summary of historical patterns
            - confidence_score: Confidence in the pattern (0-1)
    """
    
    try:
        from .find_similar_news_tool import find_similar_news_with_price_impact
        from .get_recent_news import get_recent_news_items
        
        eastern_tz = pytz.timezone('America/New_York')
        
        # 1. Find similar historical news with price impacts
        print(f"\n=== Analyzing {ticker} ===")
        print("  → Finding similar historical news...")
        
        similar_news = find_similar_news_with_price_impact(
            ticker=ticker,
            search_query=news_text,
            timestamp=timestamp,
            db_manager=db_manager,
            top_k=top_k,
            window_minutes=window_minutes
        )
        
        # 2. Get recent news for this ticker (last 24 hours)
        print(f"  → Fetching recent news for {ticker}...")
        recent_news = get_recent_news_items(
            timestamp=timestamp,
            db_manager=db_manager,
            hours=24,
            ticker=ticker,
            limit=5
        )
        
        # 3. Calculate statistics from similar news
        price_statistics = {}
        pattern_summary = {}
        confidence_score = 0.0
        most_recent_similar = None
        
        if similar_news:
            # Extract price changes
            price_changes = [news['price_change_pct_40min'] for news in similar_news]
            highest_changes = [news['highest_price_pct'] for news in similar_news]
            lowest_changes = [news['lowest_price_pct'] for news in similar_news]
            
            # Calculate statistics
            price_statistics = {
                'avg_price_change': round(statistics.mean(price_changes), 2),
                'median_price_change': round(statistics.median(price_changes), 2),
                'max_price_change': round(max(price_changes), 2),
                'min_price_change': round(min(price_changes), 2),
                'avg_highest_point': round(statistics.mean(highest_changes), 2),
                'avg_lowest_point': round(statistics.mean(lowest_changes), 2),
                'std_deviation': round(statistics.stdev(price_changes), 2) if len(price_changes) > 1 else 0.0
            }
            
            # Pattern analysis
            positive_moves = sum(1 for pc in price_changes if pc > 0)
            negative_moves = sum(1 for pc in price_changes if pc < 0)
            neutral_moves = sum(1 for pc in price_changes if pc == 0)
            
            pattern_summary = {
                'total_events': len(similar_news),
                'positive_moves': positive_moves,
                'negative_moves': negative_moves,
                'neutral_moves': neutral_moves,
                'positive_rate': round(positive_moves / len(similar_news) * 100, 1),
                'dominant_direction': 'bullish' if positive_moves > negative_moves else 'bearish' if negative_moves > positive_moves else 'neutral',
                'volatility_level': 'high' if price_statistics['std_deviation'] > 2.0 else 'medium' if price_statistics['std_deviation'] > 1.0 else 'low'
            }
            
            # Confidence score based on:
            # - Number of similar events (more = higher confidence)
            # - Consistency of direction (more consistent = higher confidence)
            # - Recency of events (more recent = higher confidence)
            event_score = min(len(similar_news) / 10.0, 1.0)  # Max at 10 events
            consistency_score = max(positive_moves, negative_moves) / len(similar_news)
            
            confidence_score = round((event_score * 0.6 + consistency_score * 0.4), 2)
            
            # Find most recent similar news
            most_recent_similar = max(similar_news, key=lambda x: x['published'])
            
            print(f"✓ Found {len(similar_news)} similar events")
            print(f"  Pattern: {pattern_summary['dominant_direction']} ({pattern_summary['positive_rate']}% positive)")
            print(f"  Avg change: {price_statistics['avg_price_change']}%")
            print(f"  Confidence: {confidence_score}")
        else:
            print(f"⚠ No similar historical events found for {ticker}")
        
        # 4. Compile results
        result = {
            'ticker': ticker,
            'similar_news_count': len(similar_news),
            'similar_news': similar_news,
            'most_recent_similar': most_recent_similar,
            'recent_news': recent_news,
            'price_statistics': price_statistics,
            'pattern_summary': pattern_summary,
            'confidence_score': confidence_score,
            'analysis_timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
        }
        
        return result
        
    except Exception as e:
        print(f"Error analyzing ticker {ticker}: {e}")
        import traceback
        traceback.print_exc()
        return {
            'ticker': ticker,
            'error': str(e),
            'similar_news_count': 0,
            'similar_news': [],
            'most_recent_similar': None,
            'recent_news': [],
            'price_statistics': {},
            'pattern_summary': {},
            'confidence_score': 0.0
        }


def get_ticker_historical_context(
    ticker: str,
    timestamp: datetime,
    db_manager: Any,
    lookback_days: int = 7
) -> Dict[str, Any]:
    """
    Get historical price context for a ticker
    
    Args:
        ticker: Stock ticker symbol
        timestamp: Current timestamp
        db_manager: Database manager instance
        lookback_days: Days to look back (default: 7)
        
    Returns:
        Dict with historical price data and statistics
    """
    
    try:
        # Get historical prices
        price_df = db_manager.get_historical_prices(
            ticker=ticker,
            timestamp=timestamp,
            lookback_days=lookback_days
        )
        
        if price_df is None or price_df.empty:
            return {
                'ticker': ticker,
                'has_data': False,
                'message': 'No historical price data available'
            }
        
        # Calculate statistics
        current_price = price_df.iloc[-1]['close']
        price_7d_ago = price_df.iloc[0]['close']
        price_change_7d = ((current_price - price_7d_ago) / price_7d_ago) * 100
        
        high_7d = price_df['high'].max()
        low_7d = price_df['low'].min()
        avg_volume = price_df['volume'].mean()
        
        return {
            'ticker': ticker,
            'has_data': True,
            'current_price': float(current_price),
            'price_change_7d_pct': round(price_change_7d, 2),
            'high_7d': float(high_7d),
            'low_7d': float(low_7d),
            'avg_volume_7d': int(avg_volume),
            'data_points': len(price_df)
        }
        
    except Exception as e:
        print(f"Error getting historical context for {ticker}: {e}")
        return {
            'ticker': ticker,
            'has_data': False,
            'error': str(e)
        }


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_ticker_analysis_tool(timestamp: datetime, db_manager: Any, news_text: str):
    """Create ticker_analysis tool with captured context"""
    
    @tool
    def ticker_analysis_tool(ticker: str, top_k: int = 10) -> str:
        """
        Comprehensive ticker analysis with similar historical news and price patterns.
        Use this to understand how similar news affected this ticker in the past.
        
        This tool provides:
        - Similar historical news with price impacts (40-min window)
        - Most recent similar event
        - Statistical analysis (avg/median price change, volatility)
        - Pattern summary (bullish/bearish tendency)
        - Recent news for the ticker (last 24h)
        - Confidence score for the pattern
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT", "GOOGL")
            top_k: Number of similar historical events to analyze (default: 10)
        
        Returns:
            JSON string with comprehensive ticker analysis including:
            - similar_news: List of similar events with price impacts
            - price_statistics: avg_price_change, median, max, min, std_deviation
            - pattern_summary: dominant_direction, positive_rate, volatility_level
            - most_recent_similar: Most recent similar event
            - recent_news: Recent news for this ticker
            - confidence_score: Confidence in the pattern (0-1)
        """
        try:
            import json
            
            result = analyze_ticker_with_similar_news(
                ticker=ticker,
                news_text=news_text,
                timestamp=timestamp,
                db_manager=db_manager,
                top_k=top_k
            )
            
            return json.dumps(result, default=str)
        except Exception as e:
            import json
            return json.dumps({"error": str(e), "ticker": ticker})
    
    return ticker_analysis_tool


def create_ticker_historical_context_tool(timestamp: datetime, db_manager: Any):
    """Create ticker_historical_context tool with captured context"""
    
    @tool
    def ticker_historical_context_tool(ticker: str, lookback_days: int = 7) -> str:
        """
        Get historical price context for a ticker.
        Use this to understand recent price movements and trends.
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            lookback_days: Days to look back (default: 7)
        
        Returns:
            JSON string with historical price data:
            - current_price: Current price
            - price_change_7d_pct: 7-day price change percentage
            - high_7d, low_7d: 7-day high and low
            - avg_volume_7d: Average volume
        """
        try:
            import json
            
            result = get_ticker_historical_context(
                ticker=ticker,
                timestamp=timestamp,
                db_manager=db_manager,
                lookback_days=lookback_days
            )
            
            return json.dumps(result, default=str)
        except Exception as e:
            import json
            return json.dumps({"error": str(e), "ticker": ticker})
    
    return ticker_historical_context_tool

