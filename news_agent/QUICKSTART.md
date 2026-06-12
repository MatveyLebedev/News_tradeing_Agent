# Quick Start Guide

## Installation (5 minutes)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
# Copy example environment file
cp env.example .env

# Edit .env with your credentials
# Required:
# - DB_HOST, DB_USER, DB_PASSWORD
# - GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_API_KEY
# Optional:
# - LANGCHAIN_API_KEY (for tracing)
```

### 3. Verify Installation
```bash
python news_agent/test_agent.py
```

---

## Basic Usage (2 minutes)

### Minimal Example
```python
from news_agent import TradingAgent
from datetime import datetime

# Initialize
with TradingAgent() as agent:
    # Analyze news
    signals = agent.run(
        news_text="Apple announces breakthrough M4 chip with 40% performance gain",
        timestamp=datetime(2024, 11, 1, 14, 30)
    )
    
    # Print results
    for signal in signals:
        print(f"{signal['ticker']}: {signal['signal']} @ ${signal['stop_loss']:.2f} - ${signal['take_profit']:.2f}")
```

---

## Input Format

```python
agent.run(
    news_text="Your news article text here...",
    timestamp=datetime(2024, 10, 1, 14, 30)  # Must be historical, not future
)
```

**Important**: Timestamp must be in the past to prevent data leakage.

---

## Output Format

```python
[
    {
        "ticker": "AAPL",
        "signal": "BUY",           # or "SELL" or "HOLD"
        "stop_loss": 150.25,
        "take_profit": 165.50,
        "confidence": 0.85,        # 0-1 scale
        "reasoning": "Strong positive sentiment (85/100) on breakthrough announcement..."
    },
    # ... more signals
]
```

---

## Common Issues

### No signals generated?
- **Normal if**: No historical data exists for identified tickers
- **Check**: `confidence_threshold` in config (default: 0.7)
- **Solution**: Lower threshold or ensure data exists

### Database connection error?
- **Check**: Database credentials in `.env`
- **Check**: Database is accessible
- **Check**: Tables exist: `historical_data_aapl`, etc.

### Import errors?
- **Run**: `pip install -r requirements.txt`
- **Check**: Python version >= 3.9

### Ticker not found?
- **Check**: Table `historical_data_{ticker}` exists in database
- **Check**: Historical data available for your timestamp

---

## Next Steps

### Run Examples
```bash
python news_agent/example_usage.py
```

### Enable Tracing (Optional)
1. Sign up at https://smith.langchain.com
2. Get API key
3. Add to `.env`:
```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_key_here
```

### Read Full Docs
- `README.md` - Complete documentation
- `IMPLEMENTATION_SUMMARY.md` - Architecture details

---

## Quick Configuration

### Adjust Confidence Threshold
```python
from news_agent import TradingAgent, AgentConfig

config = AgentConfig()
config.CONFIDENCE_THRESHOLD = 0.6  # Lower = more signals

agent = TradingAgent(config=config)
```

### Adjust Risk Parameters
```python
config = AgentConfig()
config.ATR_MULTIPLIER = 2.5       # Wider stop loss
config.RISK_REWARD_RATIO = 3.0    # Higher profit target

agent = TradingAgent(config=config)
```

---

## Support

For detailed documentation, see `README.md`

For implementation details, see `IMPLEMENTATION_SUMMARY.md`

For issues or questions, check the main project repository.



