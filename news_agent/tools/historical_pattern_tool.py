"""
Tool for finding similar historical news patterns using vector embeddings
All timestamps use US Eastern Time (America/New_York)

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import os
import sys
from pathlib import Path
from langchain_openai import OpenAIEmbeddings
import pytz

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import EMBEDDING_MODEL


def find_similar_historical_news(
    news_text: str,
    ticker: str,
    timestamp: datetime,
    db_manager: Any,
    embedding_model_name: str = EMBEDDING_MODEL,
    top_k: int = 5,
    min_time_diff_hours: int = 1,
    market_open_hour: int = 9,
    market_open_minute: int = 30,
    market_close_hour: int = 16,
    min_time_before_close: int = 60
) -> List[Dict[str, Any]]:
    """
    Find similar historical news using vector embeddings and analyze their price impact
    
    Only returns news published during market hours and measures 40-minute price impact
    Prevents data leakage by ensuring historical news is at least 1h before current timestamp
    
    Args:
        news_text: Current news text to find similar patterns
        ticker: Stock ticker to filter for
        timestamp: Current news timestamp
        db_manager: Database manager instance
        embedding_model_name: Embedding model to use
        top_k: Number of similar news to return
        min_time_diff_hours: Minimum hours before current timestamp
        market_open_hour: Market open hour
        market_open_minute: Market open minute
        market_close_hour: Market close hour
        min_time_before_close: Minimum minutes before market close
        
    Returns:
        List of similar news with their price impacts
    """
    
    try:
        # Initialize embeddings model
        embeddings = OpenAIEmbeddings(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            model=embedding_model_name,
            dimensions=1536,
            check_embedding_ctx_length=False,
        )
        
        # Generate embedding for current news
        query_embedding = embeddings.embed_query(news_text)
        
        # Search for similar news
        similar_news = db_manager.vector_similarity_search(
            query_embedding=query_embedding,
            timestamp=timestamp,
            ticker=ticker,
            limit=top_k * 2,  # Get more to filter by market hours
            min_time_diff_hours=min_time_diff_hours
        )
        
        if not similar_news:
            print(f"No similar historical news found for {ticker}")
            return []
        
        # Filter and enrich with price impact
        eastern_tz = pytz.timezone('America/New_York')
        results = []
        for news in similar_news:
            published_time = news['published']
            
            # Ensure published_time is timezone-aware (Eastern Time)
            if published_time.tzinfo is None:
                published_time = eastern_tz.localize(published_time)
            else:
                published_time = published_time.astimezone(eastern_tz)
            
            # Check if published during market hours (Eastern Time)
            market_open = published_time.replace(
                hour=market_open_hour,
                minute=market_open_minute,
                second=0
            )
            market_close = published_time.replace(
                hour=market_close_hour,
                minute=0,
                second=0
            )
            min_time_cutoff = market_close - timedelta(minutes=min_time_before_close)
            
            is_market_hours = market_open <= published_time <= min_time_cutoff
            
            if not is_market_hours:
                continue  # Skip news not during market hours
            
            # Calculate price movement 40 minutes after publication
            price_impact = calculate_price_movement_40min(
                ticker=ticker,
                published_time=published_time,
                db_manager=db_manager
            )
            
            if price_impact:
                results.append({
                    'id': news['id'],
                    'title': news['title'],
                    'summary': news['summary'],
                    'published': published_time,
                    'similarity': news['similarity'],
                    'price_change_pct': price_impact['change_percentage'],
                    'price_change_abs': price_impact['change_absolute'],
                    'direction': price_impact['direction']
                })
            
            # Stop if we have enough results
            if len(results) >= top_k:
                break
        
        print(f"✓ Found {len(results)} similar historical news patterns for {ticker}")
        return results
        
    except Exception as e:
        print(f"Error finding similar historical news: {e}")
        return []


def calculate_price_movement_40min(
    ticker: str,
    published_time: datetime,
    db_manager: Any
) -> Optional[Dict[str, float]]:
    """
    Calculate 40-minute price movement after news publication
    
    Args:
        ticker: Stock ticker
        published_time: News publication time
        db_manager: Database manager instance
        
    Returns:
        Dict with price changes or None if data not available
    """
    
    try:
        # Get price at publication time
        price_at_news = db_manager.get_price_at_time(ticker, published_time)
        
        if not price_at_news:
            return None
        
        # Get price 40 minutes after
        time_after_40min = published_time + timedelta(minutes=40)
        price_after = db_manager.get_price_at_time(ticker, time_after_40min)
        
        if not price_after:
            return None
        
        # Calculate changes
        price_start = price_at_news['close']
        price_end = price_after['close']
        
        change_absolute = price_end - price_start
        change_percentage = (change_absolute / price_start) * 100
        
        return {
            'price_at_news': float(price_start),
            'price_after_40min': float(price_end),
            'change_absolute': float(change_absolute),
            'change_percentage': float(change_percentage),
            'direction': 'up' if change_absolute > 0 else 'down' if change_absolute < 0 else 'flat'
        }
        
    except Exception as e:
        print(f"Error calculating 40-min price movement: {e}")
        return None



