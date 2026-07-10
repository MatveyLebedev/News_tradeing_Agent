"""
Example usage of the Trading Agent

This example demonstrates:
1. Basic usage with a single news article
2. Batch processing multiple news items
3. Time-aware queries (data leakage protection)
4. LangSmith tracing integration
"""
import os
from datetime import datetime
from news_agent import TradingAgent, AgentConfig


def example_single_news():
    """Example: Analyze a single news article"""
    
    print("\n" + "="*70)
    print("EXAMPLE 1: Single News Analysis")
    print("="*70 + "\n")
    
    # Sample news text
    news_text = """
    Apple Inc. announced a revolutionary breakthrough with its M4 chip, 
    promising 40% faster performance than the previous generation. 
    The company also revealed strong iPhone 15 pre-order numbers, 
    exceeding analyst expectations. CEO Tim Cook stated this represents 
    the biggest product cycle in years, with enterprise adoption accelerating.
    """
    
    # Timestamp of news publication (use historical timestamp to test)
    timestamp = datetime(2024, 11, 1, 14, 30)  # Nov 1, 2024, 2:30 PM
    
    # Initialize agent
    agent = TradingAgent()
    
    # Analyze news
    signals = agent.run(news_text=news_text, timestamp=timestamp)
    
    # Display results
    print(f"\nGenerated {len(signals)} signal(s):\n")
    for signal in signals:
        print(f"Ticker: {signal['ticker']}")
        print(f"Signal: {signal['signal']}")
        print(f"Stop Loss: ${signal['stop_loss']:.2f}")
        print(f"Take Profit: ${signal['take_profit']:.2f}")
        print(f"Confidence: {signal['confidence']:.2%}")
        print(f"Reasoning: {signal['reasoning'][:150]}...")
        print()
    
    # Clean up
    agent.close()


def example_batch_analysis():
    """Example: Batch analyze multiple news articles"""
    
    print("\n" + "="*70)
    print("EXAMPLE 2: Batch Analysis")
    print("="*70 + "\n")
    
    # Multiple news items
    news_items = [
        {
            "text": "Tesla reported record Q3 deliveries, beating estimates by 15%.",
            "timestamp": datetime(2024, 10, 15, 10, 0)
        },
        {
            "text": "Microsoft launches new Azure AI services with GPT-4 integration.",
            "timestamp": datetime(2024, 10, 16, 11, 30)
        },
        {
            "text": "NVIDIA announces next-generation H200 GPU for AI workloads.",
            "timestamp": datetime(2024, 10, 17, 13, 0)
        }
    ]
    
    # Initialize agent
    agent = TradingAgent()
    
    # Batch analyze
    all_signals = agent.batch_analyze(news_items)
    
    # Display summary
    print("\nBatch Analysis Summary:")
    print(f"Total news items: {len(news_items)}")
    print(f"Total signals generated: {sum(len(signals) for signals in all_signals)}")
    
    for idx, signals in enumerate(all_signals, 1):
        print(f"\nNews {idx}: {len(signals)} signal(s)")
        for signal in signals:
            print(f"  - {signal['ticker']}: {signal['signal']} "
                  f"(confidence: {signal['confidence']:.2%})")
    
    # Clean up
    agent.close()


def example_custom_config():
    """Example: Use custom configuration"""
    
    print("\n" + "="*70)
    print("EXAMPLE 3: Custom Configuration")
    print("="*70 + "\n")
    
    # Create custom config with stricter confidence threshold
    config = AgentConfig()
    config.CONFIDENCE_THRESHOLD = 0.8  # Require 80% confidence
    config.ATR_MULTIPLIER = 2.5  # Wider stop loss
    config.RISK_REWARD_RATIO = 3.0  # Higher risk-reward ratio
    
    print("Custom Configuration:")
    print(f"  Confidence Threshold: {config.CONFIDENCE_THRESHOLD}")
    print(f"  ATR Multiplier: {config.ATR_MULTIPLIER}")
    print(f"  Risk-Reward Ratio: {config.RISK_REWARD_RATIO}")
    print()
    
    news_text = """
    AMD unveils new Ryzen 9000 series processors with 3D V-Cache technology,
    targeting both gaming and data center markets. Performance benchmarks show
    20-30% improvements over previous generation.
    """
    
    timestamp = datetime(2024, 10, 20, 15, 0)
    
    # Use custom config
    agent = TradingAgent(config=config)
    signals = agent.run(news_text=news_text, timestamp=timestamp)
    
    print(f"\nGenerated {len(signals)} signal(s) with stricter criteria\n")
    
    agent.close()


def example_data_leakage_protection():
    """Example: Demonstrate data leakage protection"""
    
    print("\n" + "="*70)
    print("EXAMPLE 4: Data Leakage Protection")
    print("="*70 + "\n")
    
    news_text = "Apple stock surges on earnings beat."
    
    # Try to use future timestamp (should fail)
    future_timestamp = datetime(2026, 1, 1, 10, 0)
    
    print(f"Attempting to analyze news with future timestamp: {future_timestamp}")
    print(f"Current time: {datetime.now()}")
    print()
    
    agent = TradingAgent()
    
    try:
        signals = agent.run(news_text=news_text, timestamp=future_timestamp)
    except ValueError as e:
        print("✓ Data leakage protection worked!")
        print(f"  Error: {e}")
        print("\nThis prevents the agent from accessing future data that wouldn't")
        print("have been available at the time of news publication.")
    
    agent.close()


def example_with_context_manager():
    """Example: Use context manager for automatic cleanup"""
    
    print("\n" + "="*70)
    print("EXAMPLE 5: Context Manager Usage")
    print("="*70 + "\n")
    
    news_text = "Google announces breakthrough in quantum computing."
    timestamp = datetime(2024, 10, 25, 12, 0)
    
    # Use context manager (automatically closes connections)
    with TradingAgent() as agent:
        signals = agent.run(news_text=news_text, timestamp=timestamp)
        print(f"Generated {len(signals)} signal(s)")
    
    print("\n✓ Agent automatically closed via context manager")


if __name__ == "__main__":
    """
    Run examples
    
    Before running:
    1. Ensure database is accessible
    2. Set up environment variables (copy env.example to .env)
    3. Ensure historical price data exists for test tickers
    4. (Optional) Set up LangSmith API key for tracing
    """
    
    print("\n" + "="*70)
    print("TRADING AGENT - EXAMPLE USAGE")
    print("="*70)
    
    # Check if running with proper setup
    if not os.getenv("DB_HOST"):
        print("\n⚠️  Warning: No environment variables loaded")
        print("   Copy env.example to .env and configure it first")
        print()
    
    # Run examples
    try:
        example_single_news()
        example_batch_analysis()
        example_custom_config()
        example_data_leakage_protection()
        example_with_context_manager()
        
        print("\n" + "="*70)
        print("ALL EXAMPLES COMPLETED")
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n✗ Error running examples: {e}")
        print("\nMake sure:")
        print("1. Database is accessible")
        print("2. Environment variables are set")
        print("3. Historical price data exists")
        raise



