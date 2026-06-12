"""
Tool for getting recent news articles
Helper function - not decorated with @tool as it's called directly
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from langchain_core.tools import tool
import json


def get_recent_news_items(
    timestamp: datetime,
    db_manager: Any,
    hours: int = 24,
    ticker: Optional[str] = None,
    limit: int = None
) -> List[Dict[str, Any]]:
    """
    Get recent news titles and summaries with IDs for last X hours
    
    Args:
        timestamp: Reference timestamp (only news before this)
        db_manager: Database manager instance
        hours: Hours to look back (default: 24)
        ticker: Optional ticker filter
        limit: Optional limit on number of results (default: None = all)
        
    Returns:
        List of news items with id, title, summary, published
    """
    
    try:
        news_items = db_manager.get_recent_news(timestamp, hours, ticker)
        
        # Apply limit if specified
        if limit and len(news_items) > limit:
            news_items = news_items[:limit]
        
        if news_items:
            print(f"✓ Retrieved {len(news_items)} recent news items" + 
                  (f" for {ticker}" if ticker else "") +
                  (f" (limited to {limit})" if limit else ""))
        else:
            print(f"No recent news found in last {hours} hours" + 
                  (f" for {ticker}" if ticker else ""))
        
        return news_items
        
    except Exception as e:
        print(f"Error retrieving recent news: {e}")
        return []


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_get_recent_news_tool(timestamp: datetime, db_manager: Any):
    """Create get_recent_news tool with captured context"""
    
    @tool
    def get_recent_news_tool(hours: int = 24, limit: int = 10) -> str:
        """
        Get recent news articles. Use this to check for other recent announcements.
        Returns news across all tickers.
        
        Args:
            hours: Hours to look back (default: 24)
            limit: Maximum number of news items to return (default: 10)
        
        Returns:
            JSON string with recent news items
        """
        try:
            results = get_recent_news_items(
                timestamp=timestamp,
                db_manager=db_manager,
                hours=hours,
                ticker=None,
                limit=limit
            )
            return json.dumps(results, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    return get_recent_news_tool

