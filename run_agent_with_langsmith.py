#!/usr/bin/env python3
"""
Simple script to run the trading agent with LangSmith enabled
"""
import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist

# Import LangSmith config (now loads from .env)
from config import (
    LANGCHAIN_TRACING_V2,
    LANGCHAIN_API_KEY,
    LANGCHAIN_ENDPOINT
)

# LangSmith project name for complex agent
LANGCHAIN_PROJECT = "news-trading-agent"

# Enable LangSmith tracing from config
# Force tracing on for this launcher script
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = str(LANGCHAIN_API_KEY or "")
os.environ["LANGCHAIN_PROJECT"] = str(os.getenv("LANGCHAIN_PROJECT", LANGCHAIN_PROJECT))
os.environ["LANGCHAIN_ENDPOINT"] = str(LANGCHAIN_ENDPOINT or "https://api.smith.langchain.com")

# Import after setting LangSmith env vars so AgentConfig picks up tracing settings
from news_agent import TradingAgent
from news_agent.agent_config import AgentConfig

def main():
    print("\n" + "="*70)
    print("TRADING AGENT WITH LANGSMITH")
    print("="*70)
    print("✓ LangSmith tracing: ENABLED")
    print(f"✓ Project: {LANGCHAIN_PROJECT}")
    print(f"✓ LLM Model: {AgentConfig.LLM_MODEL}")
    print("✓ View traces at: https://smith.langchain.com")
    print("="*70 + "\n")
    
    # Initialize agent
    agent = TradingAgent()
    
    # Example news
    news_text = """The feud between Anthropic and the Department of Defense ratcheted up on Friday after President Trump ordered all government agencies to immediately cease using the artificial intelligence startup's technology.

"We don’t need it, we don’t want it, and will not do business with them again!" Trump posted on Truth Social.

The president weighed in on the standoff after Anthropic CEO, Dario Amodei, said on Thursday that the company would cut ties with the government rather than lift restrictions on its AI model's usage.

"We cannot in good conscience accede to their request," Amodei wrote in response to the Defense Department's demands to use the AI model for "all lawful purposes." The government has given Anthropic until 5:01 p.m. on Friday to answer its request to use models as it sees fit.

OpenAI (OPAI.PVT) also joined the fray, with the Wall Street Journal reporting that CEO Sam Altman told his staff that the ChatGPT maker was working on a deal to help resolve the feud between the startup's rival Anthropic (ANTH.PVT) and the Department of Defense. “We would like to try to help de-escalate things,” Altman wrote in a note to employees.

OpenAI (OPAI.PVT) also announced on Friday that it raised $110 billion in a record-breaking funding round and counted SoftBank (SFTBY), Nvidia (NVDA), and Amazon (AMZN) among its backers. The startup's latest fundraising round values the company at $730 billion pre-money."""

    import pytz
    from datetime import datetime
    from datetime import datetime
    import pytz

    # Parse the published string and convert to EST
    published_str = "Sat, 27 Feb 2026 01:31:03 GMT"
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
                print(f"\n{i}. {signal.get('ticker', 'N/A')} - {signal.get('signal', 'N/A')}")
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

