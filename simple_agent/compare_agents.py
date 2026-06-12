#!/usr/bin/env python3
"""
Compare Simple Agent vs Complex Agent
"""
import os
import sys
from datetime import datetime
import pytz

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simple_agent import SimpleAgent
from news_agent import TradingAgent


def main():
    print("\n" + "="*70)
    print("AGENT COMPARISON: Simple vs Complex")
    print("="*70 + "\n")
    
    # Example news
    news_text = """When a data center cooling issue halted futures and options trading at the Chicago Mercantile Exchange, it focused attention on the hidden infrastructure that's becoming ever more critical to daily life: data centers. These facilities are essential for everything from commodities trading to uploading the photos on your smartphone to the cloud."""
    
    # Parse the published string and convert to EST
    published_str = "Thu, 28 Nov 2025 17:31:03 GMT"
    dt_utc = datetime.strptime(published_str, "%a, %d %b %Y %H:%M:%S GMT")
    dt_utc = pytz.utc.localize(dt_utc)
    est = pytz.timezone('US/Eastern')
    timestamp = dt_utc.astimezone(est)
    
    print(f"News: {news_text[:100]}...")
    print(f"Timestamp: {timestamp}\n")
    
    # Test Simple Agent
    print("\n" + "="*70)
    print("SIMPLE AGENT")
    print("="*70)
    
    try:
        simple_agent = SimpleAgent()
        simple_signals = simple_agent.run(news_text=news_text, timestamp=timestamp)
        simple_agent.close()
        
        print(f"\n✓ Simple Agent Results: {len(simple_signals)} signals")
        for i, signal in enumerate(simple_signals, 1):
            print(f"  {i}. {signal['ticker']} - {signal['signal'].upper()} ({signal['confidence']:.0%})")
    except Exception as e:
        print(f"✗ Simple Agent Error: {e}")
        simple_signals = []
    
    # Test Complex Agent
    print("\n" + "="*70)
    print("COMPLEX AGENT")
    print("="*70)
    
    try:
        complex_agent = TradingAgent()
        complex_signals = complex_agent.run(news_text=news_text, timestamp=timestamp)
        complex_agent.close()
        
        print(f"\n✓ Complex Agent Results: {len(complex_signals)} signals")
        for i, signal in enumerate(complex_signals, 1):
            print(f"  {i}. {signal['ticker']} - {signal['signal'].upper()} ({signal['confidence']:.0%})")
    except Exception as e:
        print(f"✗ Complex Agent Error: {e}")
        complex_signals = []
    
    # Summary
    print("\n" + "="*70)
    print("COMPARISON SUMMARY")
    print("="*70)
    print(f"Simple Agent:  {len(simple_signals)} signals")
    print(f"Complex Agent: {len(complex_signals)} signals")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

