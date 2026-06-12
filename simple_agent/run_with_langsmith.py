#!/usr/bin/env python3
"""
Run Simple Agent (Baseline) with LangSmith tracing enabled
"""
import os
import sys
from pathlib import Path
from datetime import datetime
import pytz

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist

# Add parent directory to path for config import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    LANGCHAIN_TRACING_V2,
    LANGCHAIN_API_KEY,
    LANGCHAIN_ENDPOINT
)
from simple_agent import SimpleAgent

# LangSmith project name for baseline agent
LANGCHAIN_PROJECT = "baseline-agent"

# Enable LangSmith tracing from config
os.environ["LANGCHAIN_TRACING_V2"] = str(LANGCHAIN_TRACING_V2)
os.environ["LANGCHAIN_API_KEY"] = LANGCHAIN_API_KEY or ""
os.environ["LANGCHAIN_PROJECT"] = LANGCHAIN_PROJECT
os.environ["LANGCHAIN_ENDPOINT"] = LANGCHAIN_ENDPOINT


def main():
    print("\n" + "="*70)
    print("BASELINE AGENT WITH LANGSMITH")
    print("="*70)
    print("✓ LangSmith tracing: ENABLED")
    print(f"✓ Project: {LANGCHAIN_PROJECT}")
    print("✓ View traces at: https://smith.langchain.com")
    print("="*70 + "\n")
    
    # Initialize agent with LangSmith enabled
    agent = SimpleAgent(enable_langsmith=True)
    
    # Example news
    news_text = """When a data center cooling issue halted futures and options trading at the Chicago Mercantile Exchange, it focused attention on the hidden infrastructure that's becoming ever more critical to daily life: data centers. These facilities are essential for everything from commodities trading to uploading the photos on your smartphone to the cloud."""
    
    # Parse the published string and convert to EST
    published_str = "Thu, 28 Nov 2025 17:31:03 GMT"
    dt_utc = datetime.strptime(published_str, "%a, %d %b %Y %H:%M:%S GMT")
    dt_utc = pytz.utc.localize(dt_utc)
    est = pytz.timezone('US/Eastern')
    timestamp = dt_utc.astimezone(est)
    
    try:
        print("\n" + "="*70)
        print("ANALYZING NEWS")
        print("="*70)
        print(f"Timestamp: {timestamp}")
        print(f"News preview: {news_text.strip()[:80]}...")
        print("="*70 + "\n")
        
        # Run analysis
        signals = agent.run(news_text=news_text, timestamp=timestamp)
        
        print("\n" + "="*70)
        print("ANALYSIS COMPLETE")
        print("="*70)
        print(f"Generated {len(signals)} signal(s)")
        print("="*70 + "\n")
        
        if signals:
            print(f"\nGenerated {len(signals)} signal(s):")
            for i, signal in enumerate(signals, 1):
                print(f"\n{i}. {signal.get('ticker', 'N/A')} - {signal.get('signal', 'N/A').upper()}")
                print(f"   Confidence: {signal.get('confidence', 0):.2%}")
                print(f"   Reasoning: {signal.get('reasoning', 'N/A')[:100]}...")
        else:
            print("\nNo signals generated")
        
        print("\n" + "="*70)
        print("✓ View detailed trace at: https://smith.langchain.com")
        print(f"  Project: {LANGCHAIN_PROJECT}")
        print("="*70 + "\n")
        
    finally:
        agent.close()


if __name__ == "__main__":
    main()

