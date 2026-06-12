# Simple Trading Agent

A simplified version of the trading agent that makes a **single LLM call** with recent news as context.

## Features

- ✅ Single LLM call (no complex graph, no tool calling)
- ✅ Uses recent news as context (last 24 hours)
- ✅ Same input/output format as the complex agent
- ✅ **US market only** - Automatically filters non-US tickers (.TO, .L, .PA, etc.)
- ✅ Ticker validation and filtering
- ✅ Lightweight and fast
- ✅ Easy to understand and modify

## Input Format

```python
agent.run(
    news_text: str,        # News article text to analyze
    timestamp: datetime    # Publication timestamp (ET or timezone-aware)
)
```

## Output Format

Returns a list of trading signals:

```python
[
    {
        "ticker": "AAPL",
        "signal": "buy",           # "buy" or "sell"
        "confidence": 0.85,        # 0.0 to 1.0
        "reasoning": "News positive effect: Major product breakthrough..."
    }
]
```

## Usage

### Basic Usage

```python
from simple_agent import SimpleAgent
from datetime import datetime
import pytz

# Initialize agent
agent = SimpleAgent()

# Prepare news
news_text = "Apple announces breakthrough M4 chip..."
timestamp = datetime(2025, 11, 28, 14, 30)

# Run analysis
signals = agent.run(news_text=news_text, timestamp=timestamp)

# Print results
for signal in signals:
    print(f"{signal['ticker']}: {signal['signal']} ({signal['confidence']:.0%})")

# Clean up
agent.close()
```

### Using Context Manager

```python
from simple_agent import SimpleAgent

with SimpleAgent() as agent:
    signals = agent.run(news_text=news_text, timestamp=timestamp)
    # Agent automatically closes
```

### Run Example Script

```bash
cd simple_agent
python run_simple_agent.py
```

## Configuration

The agent can be configured during initialization:

```python
agent = SimpleAgent(
    model="gemini-2.0-flash-exp",  # LLM model
    temperature=0.0,                # LLM temperature
    enable_langsmith=True,          # Enable LangSmith tracing
    db_config={                     # Optional database config
        "host": "...",
        "database": "...",
        "user": "...",
        "password": "...",
        "port": "5432"
    }
)
```

### LangSmith Tracing

Enable LangSmith tracing for observability:

```python
# Set environment variables
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = "your-api-key"
os.environ["LANGCHAIN_PROJECT"] = "baseline-agent"

# Initialize with tracing enabled
agent = SimpleAgent(enable_langsmith=True)
```

Or run the example script:
```bash
python run_with_langsmith.py
```

View traces at: https://smith.langchain.com

## Environment Variables

The agent uses these environment variables (with defaults):

```bash
# Google Cloud
GOOGLE_APPLICATION_CREDENTIALS=elemental-alloy-455119-p5-cdc71d0037a1.json
LLM_PROJECT=elemental-alloy-455119-p5
LLM_LOCATION=us-central1

# Database
DB_HOST=YOUR_DB_HOST
DB_NAME=trade_agent
DB_USER=admin
DB_PASSWORD=YOUR_DB_PASSWORD
DB_PORT=5432

# LangSmith (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-api-key
LANGCHAIN_PROJECT=baseline-agent
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

## Ticker Validation

The agent automatically filters non-US market tickers:

**Filtered suffixes:**
- `.TO` - Toronto Stock Exchange (Canada)
- `.L` - London Stock Exchange
- `.PA` - Euronext Paris
- `.DE` - Deutsche Börse (Germany)
- `.HK` - Hong Kong Stock Exchange
- `.T` - Tokyo Stock Exchange
- `.AX` - Australian Securities Exchange
- `.SW` - Swiss Exchange
- `.MI` - Borsa Italiana (Italy)
- `.AS` - Euronext Amsterdam
- And 10+ more international exchanges...

**Example:**
- `AAPL` ✅ (US market - included)
- `AIR.PA` ❌ (Paris - filtered out)
- `LUN.TO` ❌ (Toronto - filtered out)

Test validation:
```bash
python test_ticker_validation.py
```

## Comparison with Complex Agent

| Feature | Simple Agent | Complex Agent |
|---------|-------------|---------------|
| LLM Calls | 1 | Multiple (graph-based) |
| Tool Calling | No | Yes (10+ tools) |
| Context | Recent news only | Recent news + similar news + technical analysis + sector analysis + risk assessment |
| Reflection | No | Yes (iterative refinement) |
| Ticker Filtering | Yes (US only) | Yes (US only) |
| Speed | Fast (~2-5 seconds) | Slower (~10-30 seconds) |
| Accuracy | Good | Better (more comprehensive) |
| Use Case | Quick analysis | Deep analysis |

## When to Use

**Use Simple Agent when:**
- You need fast results
- You want a simple, understandable system
- You don't need deep technical/sector analysis
- You're prototyping or testing

**Use Complex Agent when:**
- You need comprehensive analysis
- You want high confidence signals
- You need risk assessment and filtering
- You're running production trading systems

## Architecture

```
Input (news_text, timestamp)
    ↓
Get Recent News (DB query)
    ↓
Build Prompt (system + user + context)
    ↓
Single LLM Call
    ↓
Parse & Validate JSON Response
    ↓
Output (list of signals)
```

## Dependencies

- `google-cloud-aiplatform` - Google Vertex AI
- `psycopg2` - PostgreSQL database
- `pytz` - Timezone handling

Install with:
```bash
pip install google-cloud-aiplatform psycopg2-binary pytz
```

## Setup

1. Set up Google Cloud credentials:
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
```

Or place `elemental-alloy-455119-p5-cdc71d0037a1.json` in the parent directory.

2. Configure database (optional, uses defaults):
```bash
export DB_HOST="your-db-host"
export DB_NAME="trade_agent"
export DB_USER="admin"
export DB_PASSWORD="your-password"
```

