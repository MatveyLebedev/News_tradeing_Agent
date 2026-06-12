#!/usr/bin/env python3
"""
Batch processing script for multiple news items

Usage:
    python run_agent_batch.py
"""

from news_agent import TradingAgent
from datetime import datetime


def main():
    """Run agent on multiple news items"""
    
    print("\n" + "="*70)
    print("TRADING AGENT - BATCH PROCESSING")
    print("="*70 + "\n")
    
    # Define multiple news items to analyze
    news_items = [
        {
            "text": """
            Apple Inc. announced today a revolutionary M4 chip with 50% 
            performance improvement. The new chip features advanced AI 
            capabilities and improved power efficiency.
            """,
            "timestamp": datetime(2024, 10, 1, 14, 30),
            "source": "Bloomberg"
        },
        {
            "text": """
            Microsoft launches new AI-powered features in Office 365, 
            including advanced document analysis and automated workflow 
            suggestions. The company expects significant revenue growth.
            """,
            "timestamp": datetime(2024, 10, 1, 15, 0),
            "source": "Reuters"
        },
        {
            "text": """
            Tesla reports record quarterly deliveries, beating analyst 
            expectations by 15%. The company attributes success to improved 
            production efficiency and strong demand in China.
            """,
            "timestamp": datetime(2024, 10, 1, 15, 30),
            "source": "CNBC"
        }
    ]
    
    print(f"Processing {len(news_items)} news items...\n")
    
    # Initialize agent
    agent = TradingAgent()
    
    # Process each news item
    all_signals = []
    
    for i, item in enumerate(news_items, 1):
        print(f"\n{'='*70}")
        print(f"News Item {i}/{len(news_items)}")
        print(f"{'='*70}")
        print(f"Source: {item['source']}")
        print(f"Time: {item['timestamp'].strftime('%Y-%m-%d %H:%M')}")
        print(f"Text: {item['text'][:100]}...")
        
        try:
            signals = agent.run(
                news_text=item['text'],
                timestamp=item['timestamp'],
                metadata={
                    "source": item['source'],
                    "batch_index": i,
                    "total_items": len(news_items)
                }
            )
            
            print(f"\n✓ Generated {len(signals)} signal(s)")
            
            for signal in signals:
                signal['news_source'] = item['source']
                signal['news_time'] = item['timestamp']
                all_signals.append(signal)
                
                print(f"  - {signal['ticker']}: {signal['action']} @ ${signal['entry_price']:.2f} (confidence: {signal['confidence']:.0%})")
        
        except Exception as e:
            print(f"\n✗ Error processing news item {i}: {e}")
    
    # Close agent
    agent.close()
    
    # Summary
    print("\n" + "="*70)
    print("BATCH PROCESSING SUMMARY")
    print("="*70 + "\n")
    
    print(f"Total news items processed: {len(news_items)}")
    print(f"Total signals generated: {len(all_signals)}")
    
    if all_signals:
        print(f"\nSignals by ticker:")
        tickers = {}
        for signal in all_signals:
            ticker = signal['ticker']
            if ticker not in tickers:
                tickers[ticker] = []
            tickers[ticker].append(signal)
        
        for ticker, signals in sorted(tickers.items()):
            print(f"  {ticker}: {len(signals)} signal(s)")
            for signal in signals:
                print(f"    - {signal['action']} from {signal['news_source']} (confidence: {signal['confidence']:.0%})")
    
    print("\n" + "="*70)
    print("DONE")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

