#!/usr/bin/env python3
"""
Example script to run the simple trading agent
"""
import os
from datetime import datetime
import pytz
from simple_agent import SimpleAgent


def main():
    print("\n" + "="*70)
    print("SIMPLE TRADING AGENT")
    print("="*70)
    print("✓ Single LLM call with recent news context")
    print("✓ Same input/output format as complex agent")
    print("="*70 + "\n")
    
    # Initialize agent
    agent = SimpleAgent()
    
    # Example news
    news_text = """When a data center cooling issue halted futures and options trading at the Chicago Mercantile Exchange, it focused attention on the hidden infrastructure that's becoming ever more critical to daily life: data centers. These facilities are essential for everything from commodities trading to uploading the photos on your smartphone to the cloud."""
    
    # Parse the published string and convert to EST
    published_str = "Thu, 28 Nov 2025 17:31:03 GMT"
    dt_utc = datetime.strptime(published_str, "%a, %d %b %Y %H:%M:%S GMT")
    dt_utc = pytz.utc.localize(dt_utc)
    est = pytz.timezone('US/Eastern')
    timestamp = dt_utc.astimezone(est)
    
    try:
        # Run analysis
        signals = agent.run(news_text=news_text, timestamp=timestamp)
        
        if signals:
            print(f"\nGenerated {len(signals)} signal(s):")
            for i, signal in enumerate(signals, 1):
                print(f"\n{i}. {signal.get('ticker', 'N/A')} - {signal.get('signal', 'N/A').upper()}")
                print(f"   Confidence: {signal.get('confidence', 0):.2%}")
                print(f"   Reasoning: {signal.get('reasoning', 'N/A')}")
        else:
            print("\nNo signals generated")
        
        print("\n" + "="*70 + "\n")
        
    finally:
        agent.close()


if __name__ == "__main__":
    main()

