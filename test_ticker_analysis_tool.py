"""
Test script for the new ticker_analysis_tool
Demonstrates how to use the tool to analyze a ticker with similar news
"""
from datetime import datetime
import pytz
from news_agent.database.db_manager import DBManager
from news_agent.tools.ticker_analysis_tool import analyze_ticker_with_similar_news, get_ticker_historical_context
import json

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'bloomberg_news',
    'user': 'postgres',
    'password': 'postgres'
}


def test_ticker_analysis():
    """Test the ticker analysis tool"""
    
    print("=" * 80)
    print("Testing Ticker Analysis Tool")
    print("=" * 80)
    
    # Initialize database manager
    db_manager = DBManager(DB_CONFIG)
    
    # Test parameters
    ticker = "AAPL"
    news_text = "Apple announces new chip breakthrough with improved performance"
    timestamp = datetime.now(pytz.timezone('America/New_York'))
    
    print(f"\nTest Parameters:")
    print(f"  Ticker: {ticker}")
    print(f"  News: {news_text}")
    print(f"  Timestamp: {timestamp}")
    
    # Test 1: Comprehensive ticker analysis
    print("\n" + "=" * 80)
    print("Test 1: Comprehensive Ticker Analysis")
    print("=" * 80)
    
    result = analyze_ticker_with_similar_news(
        ticker=ticker,
        news_text=news_text,
        timestamp=timestamp,
        db_manager=db_manager,
        top_k=10
    )
    
    print("\nResults:")
    print(f"  Similar news found: {result['similar_news_count']}")
    
    if result['similar_news_count'] > 0:
        print(f"\n  Price Statistics:")
        stats = result['price_statistics']
        print(f"    Average price change: {stats.get('avg_price_change', 'N/A')}%")
        print(f"    Median price change: {stats.get('median_price_change', 'N/A')}%")
        print(f"    Max price change: {stats.get('max_price_change', 'N/A')}%")
        print(f"    Min price change: {stats.get('min_price_change', 'N/A')}%")
        print(f"    Std deviation: {stats.get('std_deviation', 'N/A')}%")
        
        print(f"\n  Pattern Summary:")
        pattern = result['pattern_summary']
        print(f"    Total events: {pattern.get('total_events', 0)}")
        print(f"    Positive moves: {pattern.get('positive_moves', 0)}")
        print(f"    Negative moves: {pattern.get('negative_moves', 0)}")
        print(f"    Positive rate: {pattern.get('positive_rate', 0)}%")
        print(f"    Dominant direction: {pattern.get('dominant_direction', 'N/A')}")
        print(f"    Volatility level: {pattern.get('volatility_level', 'N/A')}")
        
        print(f"\n  Confidence Score: {result['confidence_score']}")
        
        if result['most_recent_similar']:
            print(f"\n  Most Recent Similar Event:")
            recent = result['most_recent_similar']
            print(f"    Title: {recent['title']}")
            print(f"    Published: {recent['published']}")
            print(f"    Price change: {recent['price_change_pct_40min']}%")
            print(f"    Similarity: {recent['similarity']:.3f}")
        
        print(f"\n  Recent news for {ticker}: {len(result['recent_news'])} items")
        
        # Show first 3 similar news items
        print(f"\n  Sample Similar News (first 3):")
        for i, news in enumerate(result['similar_news'][:3], 1):
            print(f"\n    {i}. {news['title'][:80]}...")
            print(f"       Published: {news['published']}")
            print(f"       Price change: {news['price_change_pct_40min']}%")
            print(f"       Highest: {news['highest_price_pct']}%, Lowest: {news['lowest_price_pct']}%")
            print(f"       Similarity: {news['similarity']:.3f}")
    else:
        print("  No similar historical events found")
    
    # Test 2: Historical context
    print("\n" + "=" * 80)
    print("Test 2: Historical Price Context")
    print("=" * 80)
    
    context = get_ticker_historical_context(
        ticker=ticker,
        timestamp=timestamp,
        db_manager=db_manager,
        lookback_days=7
    )
    
    print("\nHistorical Context:")
    if context.get('has_data'):
        print(f"  Current price: ${context.get('current_price', 'N/A')}")
        print(f"  7-day change: {context.get('price_change_7d_pct', 'N/A')}%")
        print(f"  7-day high: ${context.get('high_7d', 'N/A')}")
        print(f"  7-day low: ${context.get('low_7d', 'N/A')}")
        print(f"  Avg volume: {context.get('avg_volume_7d', 'N/A'):,}")
        print(f"  Data points: {context.get('data_points', 0)}")
    else:
        print(f"  No data available: {context.get('message', 'Unknown error')}")
    
    # Close database connections
    db_manager.close_all_connections()
    
    print("\n" + "=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    test_ticker_analysis()

