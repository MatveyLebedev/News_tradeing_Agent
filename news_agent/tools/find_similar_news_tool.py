"""
Tool for finding similar historical news
Searches across all news samples without ticker filtering
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
import json
import pytz
import os
import sys
from pathlib import Path

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import EMBEDDING_MODEL

from .historical_pattern_tool import find_similar_historical_news


def find_similar_news(
    news_text: str,
    timestamp: datetime,
    db_manager: Any,
    top_k: int = 3
) -> List[Dict[str, Any]]:
    """
    Find similar historical news articles across all samples
    
    This searches across all news without ticker filtering and returns the most relevant ones
    
    Args:
        news_text: Current news article text
        timestamp: Current news timestamp
        db_manager: Database manager instance
        top_k: Number of similar news to return (default: 3)
        
    Returns:
        List of similar news items with:
            - id: News ID
            - title: News title
            - summary: News summary
            - published: Publication timestamp
            - similarity: Similarity score (0-1)
    """
    
    try:
        # Initialize embeddings model
        embeddings = OpenAIEmbeddings(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            model=EMBEDDING_MODEL,
            dimensions=1536,
            check_embedding_ctx_length=False,
        )
        
        # Generate embedding for current news
        query_embedding = embeddings.embed_query(news_text)
        
        # Search for similar news across all samples (no ticker filter)
        similar_news = db_manager.vector_similarity_search(
            query_embedding=query_embedding,
            timestamp=timestamp,
            ticker=None,  # Search across all tickers
            limit=top_k,
            min_time_diff_hours=1
        )
        
        if not similar_news:
            print("No similar historical news found")
            return []
        
        # Format results
        eastern_tz = pytz.timezone('America/New_York')
        results = []
        
        for news in similar_news:
            published_time = news['published']
            
            # Ensure published_time is timezone-aware (Eastern Time)
            if published_time.tzinfo is None:
                published_time = eastern_tz.localize(published_time)
            else:
                published_time = published_time.astimezone(eastern_tz)
            
            result_item = {
                'id': news['id'],
                'title': news['title'],
                'summary': news['summary'],
                'published': published_time,
                'similarity': news['similarity']
            }
            
            results.append(result_item)
        
        if results:
            print(f"✓ Found {len(results)} similar historical news items")
        else:
            print("No similar historical news found")
        
        return results
        
    except Exception as e:
        # A quota/transport failure here (e.g. OpenRouter weekly key limit -> 403)
        # would silently return [] and make the agent reason blind. Propagate it so
        # the run fails loudly instead of producing low-quality "0 similar" analysis.
        from ..llm_errors import raise_if_infra
        raise_if_infra(e, context="similar-news embedding")
        print(f"Error finding similar news: {e}")
        import traceback
        traceback.print_exc()
        return []


def find_similar_news_for_ticker(
    news_text: str,
    ticker: str,
    timestamp: datetime,
    db_manager: Any,
    top_k: int = 10
) -> List[Dict[str, Any]]:
    """
    Find similar historical news for a specific ticker
    
    Args:
        news_text: Current news article text
        ticker: Specific ticker to search for
        timestamp: Current news timestamp
        db_manager: Database manager instance
        top_k: Number of similar news to return (default: 10)
        
    Returns:
        List of similar news items for that ticker
    """
    
    try:
        similar_news = find_similar_historical_news(
            news_text=news_text,
            ticker=ticker,
            timestamp=timestamp,
            db_manager=db_manager,
            top_k=top_k
        )
        
        if similar_news:
            print(f"✓ Found {len(similar_news)} similar news items for {ticker}")
        else:
            print(f"No similar news found for {ticker}")
        
        return similar_news
        
    except Exception as e:
        print(f"Error finding similar news for {ticker}: {e}")
        return []


def compute_typical_move_pct(
    db_manager: Any,
    ticker: str,
    timestamp: datetime,
    lookback_days: int = 15,
    window_minutes: int = 40,
) -> Optional[float]:
    """
    Average absolute intraday move (%) over `window_minutes` for a ticker.

    Used to normalize news reactions: a +0.8% move means very different things for
    a calm utility vs a volatile chip stock. Returns the mean |40-min return| over
    the lookback (intraday only — overnight gaps are excluded by grouping per day).

    Returns None if no price data.
    """
    try:
        df = db_manager.get_historical_prices(ticker, timestamp, lookback_days)
        if df is None or df.empty or "close" not in df.columns:
            return None
        s = df.set_index("date")["close"].sort_index()
        bucket = s.resample(f"{window_minutes}min").last().dropna()
        moves: List[float] = []
        for _, day in bucket.groupby(bucket.index.date):
            rets = (day.pct_change().dropna().abs() * 100).tolist()
            moves.extend(rets)
        if not moves:
            return None
        return round(sum(moves) / len(moves), 3)
    except Exception as e:
        print(f"  ⚠️  typical-move calc failed for {ticker}: {e}")
        return None


def enrich_similar_news_with_reaction(
    similar_news: List[Dict[str, Any]],
    ticker_per_id: Dict[int, Optional[str]],
    db_manager: Any,
    window_minutes: int = 40,
) -> List[Dict[str, Any]]:
    """
    Enrich similar news items with 40-min market reaction for the detected ticker.

    Used by significance_check_node: given LLM-extracted ticker for each news_id,
    compute price change in the window after publication.

    Args:
        similar_news: List of items from find_similar_news (id, title, summary, published, similarity)
        ticker_per_id: Mapping news_id -> ticker (or None if no ticker)
        db_manager: Database manager instance
        window_minutes: Reaction window in minutes after publication (default 40)

    Returns:
        Same list with extra fields where price data is available:
            - ticker (str)
            - price_change_pct: % change at end of window
            - highest_price_pct: max % over window
            - lowest_price_pct: min % over window
        Items without ticker or without price data keep original fields only (plus ticker=None).
    """
    eastern_tz = pytz.timezone('America/New_York')
    enriched: List[Dict[str, Any]] = []

    for news in similar_news:
        item = dict(news)
        ticker = ticker_per_id.get(news.get('id'))
        item['ticker'] = ticker

        if not ticker:
            enriched.append(item)
            continue

        try:
            published_time = news['published']
            if hasattr(published_time, 'tzinfo'):
                if published_time.tzinfo is None:
                    published_time = eastern_tz.localize(published_time)
                else:
                    published_time = published_time.astimezone(eastern_tz)

            price_at_news = db_manager.get_price_at_time(ticker, published_time)
            if not price_at_news:
                enriched.append(item)
                continue

            price_start = price_at_news['close']
            end_time = published_time + timedelta(minutes=window_minutes)
            price_range_df = db_manager.get_price_range(ticker, published_time, end_time)

            if price_range_df is None or price_range_df.empty:
                enriched.append(item)
                continue

            price_at_end = price_range_df.iloc[-1]['close']
            item['price_change_pct'] = round(((price_at_end - price_start) / price_start) * 100, 2)
            item['highest_price_pct'] = round(((price_range_df['high'].max() - price_start) / price_start) * 100, 2)
            item['lowest_price_pct'] = round(((price_range_df['low'].min() - price_start) / price_start) * 100, 2)
        except Exception as e:
            print(f"  ⚠️  enrich_similar_news: failed for news_id={news.get('id')} ticker={ticker}: {e}")

        enriched.append(item)

    return enriched


def find_similar_news_with_price_impact(
    ticker: str,
    search_query: str,
    timestamp: datetime,
    db_manager: Any,
    top_k: int = 10,
    window_minutes: int = 40
) -> List[Dict[str, Any]]:
    """
    Find similar news for a ticker and compute price changes in 40 minutes after publication
    
    Args:
        ticker: Stock ticker symbol
        search_query: Text query to search for similar news
        timestamp: Current timestamp (for data leakage prevention)
        db_manager: Database manager instance
        top_k: Number of similar news to return (default: 10)
        window_minutes: Time window in minutes after publication (default: 40)
        
    Returns:
        List of news items with:
            - id: News ID
            - title: News title
            - summary: News summary
            - published: Publication timestamp
            - similarity: Similarity score
            - price_at_news: Price at moment of news publication
            - price_change_pct_40min: Price change percentage after 40 minutes
            - highest_price_pct: Highest price percentage in 40 min window
            - lowest_price_pct: Lowest price percentage in 40 min window
    """
    
    try:
        # Initialize embeddings model
        embeddings = OpenAIEmbeddings(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            model=EMBEDDING_MODEL,
            dimensions=1536,
            check_embedding_ctx_length=False,
        )
        
        # Generate embedding for search query
        query_embedding = embeddings.embed_query(search_query)
        
        # Search for similar news for this ticker (в title/summary)
        similar_news = db_manager.vector_similarity_search(
            query_embedding=query_embedding,
            timestamp=timestamp,
            ticker=ticker,
            limit=top_k * 2,  # Get more to filter by available price data
            min_time_diff_hours=1
        )
        # Если по тикеру в тексте ничего нет — ищем только по смыслу; цену по тикеру проверим ниже
        if not similar_news and ticker:
            similar_news = db_manager.vector_similarity_search(
                query_embedding=query_embedding,
                timestamp=timestamp,
                ticker=None,
                limit=top_k * 2,
                min_time_diff_hours=1
            )
        if not similar_news:
            print(f"No similar news found for {ticker}")
            return []
        
        # Process each news item and calculate price impact
        eastern_tz = pytz.timezone('America/New_York')
        results = []
        
        for news in similar_news:
            published_time = news['published']
            
            # Ensure published_time is timezone-aware (Eastern Time)
            if published_time.tzinfo is None:
                published_time = eastern_tz.localize(published_time)
            else:
                published_time = published_time.astimezone(eastern_tz)
            
            # Get price at news publication time
            price_at_news = db_manager.get_price_at_time(ticker, published_time)
            
            if not price_at_news:
                continue  # Skip if no price data at publication time
            
            price_start = price_at_news['close']
            
            # Get price data for 40 minutes after publication
            end_time = published_time + timedelta(minutes=window_minutes)
            price_range_df = db_manager.get_price_range(ticker, published_time, end_time)
            
            if price_range_df is None or price_range_df.empty:
                continue  # Skip if no price data in the window
            
            # Calculate price changes
            # Get price at end of window (40 minutes after)
            price_at_end = price_range_df.iloc[-1]['close']
            price_change_pct_40min = ((price_at_end - price_start) / price_start) * 100
            
            # Find highest and lowest prices in the window
            highest_price = price_range_df['high'].max()
            lowest_price = price_range_df['low'].min()
            
            highest_price_pct = ((highest_price - price_start) / price_start) * 100
            lowest_price_pct = ((lowest_price - price_start) / price_start) * 100
            
            result_item = {
                'id': news['id'],
                'title': news['title'],
                'summary': news['summary'],
                'published': published_time,
                'similarity': news['similarity'],
                'price_at_news': float(price_start),
                'price_change_pct_40min': round(price_change_pct_40min, 2),
                'highest_price_pct': round(highest_price_pct, 2),
                'lowest_price_pct': round(lowest_price_pct, 2)
            }
            
            results.append(result_item)
            
            # Stop if we have enough results
            if len(results) >= top_k:
                break
        
        if results:
            print(f"✓ Found {len(results)} similar news items with price data for {ticker}")
        else:
            print(f"No similar news with price data found for {ticker}")
        
        return results
        
    except Exception as e:
        print(f"Error finding similar news with price impact for {ticker}: {e}")
        import traceback
        traceback.print_exc()
        return []


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_find_similar_news_tool(timestamp: datetime, db_manager: Any):
    """Create find_similar_news tool with captured context"""
    
    @tool
    def find_similar_news_tool(search_query: str, top_k: int = 3) -> str:
        """
        Find similar historical news articles based on a search query.
        Use this to find past news that might be similar to the current news.
        Searches across all news samples without ticker filtering.
    
        Args:
            search_query: The text query to find similar news for (e.g., "Apple chip breakthrough" or "Microsoft AI")
            top_k: Number of similar news items to return (default: 3)
        
        Returns:
            JSON string with similar news items (id, title, summary, published, similarity)
        """
        try:
            results = find_similar_news(
                news_text=search_query,
                timestamp=timestamp,
                db_manager=db_manager,
                top_k=top_k
            )
            
            return json.dumps(results, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    return find_similar_news_tool


def create_find_similar_news_with_price_tool(timestamp: datetime, db_manager: Any):
    """Create find_similar_news_with_price tool with captured context"""
    
    @tool
    def find_similar_news_with_price_tool(ticker: str, search_query: str, top_k: int = 10) -> str:
        """
        Find similar historical news for a ticker and compute price changes in 40 minutes after publication.
        Returns news summaries with price change information including highest and lowest price points.
    
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            search_query: The text query to find similar news for (e.g., "Apple earnings" or "Microsoft AI announcement")
            top_k: Number of similar news items to return (default: 10)
        
        Returns:
            JSON string with similar news items including:
            - id, title, summary, published, similarity
            - price_at_news: Price at moment of news publication
            - price_change_pct_40min: Price change percentage after 40 minutes
            - highest_price_pct: Highest price percentage in 40 min window (from news publication)
            - lowest_price_pct: Lowest price percentage in 40 min window (from news publication)
        """
        try:
            results = find_similar_news_with_price_impact(
                ticker=ticker,
                search_query=search_query,
                timestamp=timestamp,
                db_manager=db_manager,
                top_k=top_k
            )
            
            return json.dumps(results, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    return find_similar_news_with_price_tool

