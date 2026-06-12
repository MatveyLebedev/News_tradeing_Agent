#!/usr/bin/env python3
"""
Simple script to run the trading agent

Usage:
    python run_agent.py
"""

from news_agent import TradingAgent
from datetime import datetime


def main():
    """Run the trading agent with example news"""
    
    print("\n" + "="*70)
    print("TRADING AGENT - SIMPLE RUN")
    print("="*70 + "\n")
    
    # Initialize agent
    print("Initializing agent...")
    agent = TradingAgent()
    
    # Example news to analyze
    news_text = """
    Apple Inc. announced today a revolutionary M4 chip with 50% performance 
    improvement over the M3. The new chip features advanced AI capabilities, 
    improved power efficiency, and enhanced graphics performance. Industry 
    analysts expect this to significantly boost Mac sales in Q4 2024.
    """
    
    # News timestamp (when it was published)
    timestamp = datetime(2024, 10, 1, 14, 30)  # October 1, 2024 at 2:30 PM ET
    
    print(f"Analyzing news from: {timestamp}")
    print(f"News preview: {news_text[:100]}...\n")
    
    # Run analysis
    signals = agent.run(
        news_text=news_text,
        timestamp=timestamp
    )
    
    # Display results
    print("\n" + "="*70)
    print("RESULTS")
    print("="*70 + "\n")
    
    if signals:
        print(f"✓ Generated {len(signals)} trading signal(s):\n")
        
        for i, signal in enumerate(signals, 1):
            action = signal.get("action") or signal.get("signal", "n/a")
            confidence = signal.get("confidence")
            confidence_text = f"{float(confidence):.1%}" if confidence is not None else "n/a"
            reasoning = signal.get("reasoning", "")

            print(f"Signal #{i}:")
            print(f"  Ticker:       {signal.get('ticker', 'n/a')}")
            print(f"  Action:       {action}")
            if "entry_price" in signal:
                print(f"  Entry Price:  ${signal['entry_price']:.2f}")
            if "stop_loss" in signal:
                print(f"  Stop Loss:    ${signal['stop_loss']:.2f}")
            if "take_profit" in signal:
                print(f"  Take Profit:  ${signal['take_profit']:.2f}")
            print(f"  Confidence:   {confidence_text}")
            print(f"  Reasoning:    {reasoning[:100]}...")
            print()
    else:
        print("✗ No trading signals generated")
        print("  (News may not meet confidence threshold or risk criteria)")
    
    # Close agent
    agent.close()
    
    print("="*70)
    print("DONE")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()

