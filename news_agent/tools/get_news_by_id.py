"""
Tool for getting news by ID with price impact analysis
All timestamps use US Eastern Time (America/New_York)

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import pytz


def get_news_with_price_impact(
    news_id: int,
    db_manager: Any,
    price_window_minutes: int = 40,
    market_open_hour: int = 9,
    market_open_minute: int = 30,
    market_close_hour: int = 16,
    min_time_before_close: int = 60
) -> Optional[Dict[str, Any]]:
    """
    Get news article by ID with price movement analysis
    
    Only computes price movement for news published during market hours
    and before last hour of trading
    
    Args:
        news_id: Database ID of news article
        db_manager: Database manager instance
        price_window_minutes: Minutes after publication to measure impact (default: 40)
        market_open_hour: Market open hour (default: 9)
        market_open_minute: Market open minute (default: 30)
        market_close_hour: Market close hour (default: 16)
        min_time_before_close: Minimum minutes before close (default: 60)
        
    Returns:
        Dict with news data and price impact (if applicable)
    """
    
    try:
        # Get news article
        news = db_manager.get_news_by_id(news_id)
        
        if not news:
            print(f"News ID {news_id} not found")
            return None
        
        published_time = news['published']
        
        # Ensure published_time is timezone-aware (Eastern Time)
        eastern_tz = pytz.timezone('America/New_York')
        if published_time.tzinfo is None:
            published_time = eastern_tz.localize(published_time)
        else:
            published_time = published_time.astimezone(eastern_tz)
        
        # Check if published during market hours (Eastern Time)
        market_open = published_time.replace(hour=market_open_hour, minute=market_open_minute, second=0)
        market_close = published_time.replace(hour=market_close_hour, minute=0, second=0)
        min_time_cutoff = market_close - timedelta(minutes=min_time_before_close)
        
        is_market_hours = market_open <= published_time <= min_time_cutoff
        
        result = {
            'id': news['id'],
            'title': news['title'],
            'summary': news['summary'],
            'published': published_time,
            'full_text': news['full_text'],
            'is_market_hours': is_market_hours
        }
        
        # Only calculate price impact if during market hours
        if is_market_hours and news.get('full_text'):
            # Try to extract ticker from news (simplified - in production use NER)
            # For now, we'll need the ticker to be passed separately or extracted
            # This is a placeholder - price impact would need ticker information
            result['price_impact_available'] = True
        else:
            result['price_impact_available'] = False
            result['reason'] = 'Not published during market hours' if not is_market_hours else 'No ticker identified'
        
        return result
        
    except Exception as e:
        print(f"Error getting news with price impact: {e}")
        return None


def calculate_price_movement(
    ticker: str,
    published_time: datetime,
    window_minutes: int,
    db_manager: Any
) -> Optional[Dict[str, float]]:
    """
    Calculate price movement within window after news publication
    
    Args:
        ticker: Stock ticker
        published_time: News publication time
        window_minutes: Time window in minutes
        db_manager: Database manager instance
        
    Returns:
        Dict with percentage and absolute price changes
    """
    
    try:
        # Get price at publication time
        price_at_news = db_manager.get_price_at_time(ticker, published_time)
        
        if not price_at_news:
            return None
        
        # Get price after window
        end_time = published_time + timedelta(minutes=window_minutes)
        price_after = db_manager.get_price_at_time(ticker, end_time)
        
        if not price_after:
            return None
        
        # Calculate changes
        price_start = price_at_news['close']
        price_end = price_after['close']
        
        change_absolute = price_end - price_start
        change_percentage = (change_absolute / price_start) * 100
        
        return {
            'price_at_news': float(price_start),
            'price_after_window': float(price_end),
            'change_absolute': float(change_absolute),
            'change_percentage': float(change_percentage),
            'direction': 'up' if change_absolute > 0 else 'down' if change_absolute < 0 else 'flat'
        }
        
    except Exception as e:
        print(f"Error calculating price movement: {e}")
        return None



