#!/usr/bin/env python3
"""
Quick test of batch analysis functionality
"""
import os
import sys
from datetime import datetime
import pytz

# Add parent directory to path for imports (batch_analysis is subdirectory)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batch_analyze_news import BatchAnalyzer


def test_basic_functionality():
    """Test basic functionality without running full analysis"""
    print("\n" + "="*70)
    print("TESTING BATCH ANALYZER")
    print("="*70 + "\n")
    
    analyzer = BatchAnalyzer()
    
    try:
        # Test 1: Database connection
        print("Test 1: Database connection...")
        conn = analyzer.connect_db()
        print("  ✓ Connected to database")
        
        # Test 2: Table creation
        print("\nTest 2: Create results table...")
        analyzer.create_results_table()
        print("  ✓ Table ready")
        
        # Test 3: Count news
        print("\nTest 3: Count news...")
        eastern = pytz.timezone('US/Eastern')
        start_date = eastern.localize(datetime(2025, 11, 28))
        end_date = eastern.localize(datetime(2025, 11, 29))
        
        count = analyzer.count_news(start_date, end_date)
        print(f"  ✓ Found {count} news items for 2025-11-28")
        
        # Test 4: Get news
        print("\nTest 4: Retrieve news...")
        news_items = analyzer.get_news(start_date, end_date, limit=3)
        print(f"  ✓ Retrieved {len(news_items)} news items")
        
        if news_items:
            print("\n  Sample news:")
            for i, item in enumerate(news_items[:2], 1):
                print(f"    {i}. {item['title'][:50]}...")
                print(f"       Published: {item['published']}")
        
        # Test 5: Agent initialization
        print("\nTest 5: Initialize simple agent...")
        analyzer.initialize_agent('simple')
        print(f"  ✓ Initialized {analyzer.agent_name} v{analyzer.agent_version}")
        
        print("\n" + "="*70)
        print("✓ ALL TESTS PASSED")
        print("="*70)
        print("\nReady to run batch analysis!")
        print("Run: python batch_analyze_news.py")
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        analyzer.close()


if __name__ == "__main__":
    test_basic_functionality()




