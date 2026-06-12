#!/usr/bin/env python3
"""
Simplest possible script to run the trading agent

Usage:
    python run_agent_simple.py
"""

from news_agent import TradingAgent
from datetime import datetime

# Initialize agent
agent = TradingAgent()

# Analyze news
signals = agent.run(
    news_text="Apple announces breakthrough M4 chip with 50% performance boost",
    timestamp=datetime(2024, 10, 1, 14, 30)
)

# Print results
print(f"\nGenerated {len(signals)} signal(s):")
for signal in signals:
    print(f"  {signal['ticker']}: {signal['action']} @ ${signal['entry_price']:.2f} (confidence: {signal['confidence']:.0%})")

# Close
agent.close()

