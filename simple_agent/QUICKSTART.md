# Simple Agent - Quick Start Guide

## Installation

```bash
cd simple_agent
pip install -r requirements.txt
```

## Setup

Make sure the Google Cloud credentials file exists in the parent directory:
```bash
ls ../elemental-alloy-455119-p5-cdc71d0037a1.json
```

## Run Examples

### 1. Basic Example
```bash
python run_simple_agent.py
```

### 2. With LangSmith Tracing
```bash
python run_with_langsmith.py
```
View traces at: https://smith.langchain.com (Project: baseline-agent)

### 3. Test Format Validation
```bash
python test_format.py
```

### 4. Test Ticker Validation
```bash
python test_ticker_validation.py
```

### 5. Compare with Complex Agent
```bash
python compare_agents.py
```

## Python Usage

### Basic Usage

```python
from simple_agent import SimpleAgent
from datetime import datetime
import pytz

# Initialize
agent = SimpleAgent()

# Prepare input
news_text = "Your news article here..."
timestamp = datetime(2025, 11, 28, 14, 30)
timestamp = pytz.timezone('US/Eastern').localize(timestamp)

# Run analysis
signals = agent.run(news_text=news_text, timestamp=timestamp)

# Print results
for signal in signals:
    print(f"{signal['ticker']}: {signal['signal']} ({signal['confidence']:.0%})")
    print(f"  Reasoning: {signal['reasoning']}")

# Clean up
agent.close()
```

### With LangSmith Tracing

```python
import os
from simple_agent import SimpleAgent

# Enable LangSmith
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = "your-api-key"
os.environ["LANGCHAIN_PROJECT"] = "baseline-agent"

# Initialize with tracing
agent = SimpleAgent(enable_langsmith=True)

# Run analysis (will be traced)
signals = agent.run(news_text=news_text, timestamp=timestamp)

agent.close()
```

## Output Format

```python
[
    {
        "ticker": "AAPL",
        "signal": "buy",        # or "sell"
        "confidence": 0.85,     # 0.0 to 1.0
        "reasoning": "News positive effect: ..."
    }
]
```

## Key Differences from Complex Agent

| Feature | Simple | Complex |
|---------|--------|---------|
| LLM Calls | 1 | 5-15+ |
| Speed | ~2-5s | ~10-30s |
| Tools | No | Yes (10+) |
| Reflection | No | Yes |

## Troubleshooting

### Error: "GOOGLE_APPLICATION_CREDENTIALS not set"
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
```

### Error: "Database connection failed"
Check database configuration:
```bash
export DB_HOST="YOUR_DB_HOST"
export DB_NAME="trade_agent"
export DB_USER="admin"
export DB_PASSWORD="your-password"
```

### Error: "No recent news found"
The database might be empty or the timestamp is too far in the past.

## Support

For more details, see:
- `README.md` - Full documentation
- `IMPLEMENTATION_SUMMARY.md` - Technical details
- `simple_agent.py` - Source code (well commented)

