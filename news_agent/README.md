># News Trading Agent

AI-powered trading agent built with LangGraph and LangChain that analyzes news articles and generates trade signals with confidence scores.

**⏰ All timestamps use US Eastern Time (America/New_York)** - See [TIMEZONE_GUIDE.md](TIMEZONE_GUIDE.md) for details.

## Features

- **Multi-Ticker Analysis**: Automatically identifies and analyzes all tickers mentioned in news
- **Time-Aware Queries**: Prevents data leakage by only using data available before news timestamp
- **Timezone Consistency**: All timestamps in US Eastern Time (market hours timezone)
- **Comprehensive Analysis**:
  - Sentiment analysis using LLM
  - Technical indicators (ATR, RSI, Moving Averages, Bollinger Bands)
  - Sector and competitor analysis
  - Historical pattern matching using vector embeddings
  - Volatility and volume spike detection
  - Risk assessment with multiple factors
- **LangSmith Integration**: Full observability with tracing and monitoring
- **Confidence-Based Filtering**: Only returns signals meeting confidence threshold
- **PostgreSQL Vector Storage**: Uses pgvector for historical pattern matching

## Architecture

### Agent Flow

```
1. Ticker Extraction → Extract all relevant tickers from news
2. Sentiment Analysis → Analyze sentiment for each ticker
3. Data Retrieval → Fetch historical price data (time-aware)
4. For each ticker:
   ├─ Recent Context → Check for recent news cascade
   ├─ Technical Analysis → Calculate indicators
   ├─ Sector Analysis → Analyze sector trends
   ├─ Pattern Matching → Find similar historical events
   ├─ Risk Assessment → Evaluate risk (includes volatility detection)
   ├─ Signal Generation → Generate trade signal with LLM
   └─ Confidence Evaluation → Calculate confidence score
5. Aggregation → Return all signals with confidence > threshold
```

### Components

```
news_agent/
├── trading_agent.py         # Main entry point
├── agent_config.py          # Configuration
├── agent/
│   ├── state.py            # State schema
│   ├── nodes.py            # Processing nodes (12 nodes)
│   └── graph.py            # LangGraph definition
├── tools/                   # 10 specialized tools
│   ├── ticker_extraction_tool.py
│   ├── db_query_tool.py
│   ├── technical_analysis_tool.py
│   ├── sentiment_analysis_tool.py
│   ├── sector_competitor_tool.py
│   ├── historical_pattern_tool.py
│   ├── risk_assessment_tool.py
│   ├── volatility_volume_detector_tool.py
│   ├── get_news_by_id.py
│   └── get_recent_news.py
└── database/
    └── db_manager.py        # Time-aware DB queries
```

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp env.example .env
# Edit .env with your credentials
```

## Configuration

Key environment variables:

```bash
# Database
DB_HOST=your_postgres_host
DB_NAME=news
DB_USER=admin
DB_PASSWORD=your_password

# Google AI
GOOGLE_APPLICATION_CREDENTIALS=path/to/credentials.json
GOOGLE_API_KEY=your_api_key
LLM_MODEL=gemini-2.0-flash

# LangSmith (optional, for tracing)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key
LANGCHAIN_PROJECT=news-trading-agent

# Trading Parameters
CONFIDENCE_THRESHOLD=0.7      # Minimum confidence for signals
ATR_MULTIPLIER=2.0           # Stop-loss based on ATR
RISK_REWARD_RATIO=2.0        # Take-profit calculation
```

## Usage

### Basic Usage

```python
from news_agent import TradingAgent
from datetime import datetime

# Initialize agent
agent = TradingAgent()

# Analyze news
news = "Apple announces breakthrough M4 chip with 40% performance gain..."
timestamp = datetime(2024, 11, 1, 14, 30)

signals = agent.run(news_text=news, timestamp=timestamp)

# Process signals
for signal in signals:
    print(f"Ticker: {signal['ticker']}")
    print(f"Signal: {signal['signal']}")        # BUY, SELL, or HOLD
    print(f"Stop Loss: ${signal['stop_loss']}")
    print(f"Take Profit: ${signal['take_profit']}")
    print(f"Confidence: {signal['confidence']:.2%}")
    print(f"Reasoning: {signal['reasoning']}")

agent.close()
```

### Context Manager (Recommended)

```python
from news_agent import TradingAgent
from datetime import datetime

with TradingAgent() as agent:
    signals = agent.run(news_text="...", timestamp=datetime.now())
    # Process signals...
# Automatically closes database connections
```

### Batch Processing

```python
from news_agent import TradingAgent
from datetime import datetime

news_items = [
    {"text": "Tesla reports record Q3...", "timestamp": datetime(2024, 10, 15, 10, 0)},
    {"text": "Microsoft launches...", "timestamp": datetime(2024, 10, 16, 11, 30)},
]

with TradingAgent() as agent:
    all_signals = agent.batch_analyze(news_items)
    # Returns List[List[Dict]] - one signal list per news item
```

### Custom Configuration

```python
from news_agent import TradingAgent, AgentConfig

config = AgentConfig()
config.CONFIDENCE_THRESHOLD = 0.8  # Stricter confidence requirement
config.ATR_MULTIPLIER = 2.5        # Wider stop loss
config.RISK_REWARD_RATIO = 3.0     # Higher reward target

agent = TradingAgent(config=config)
```

## Output Format

Each signal is a dictionary with:

```python
{
    "ticker": "AAPL",
    "signal": "BUY",  # or "SELL" or "HOLD"
    "stop_loss": 150.25,
    "take_profit": 165.50,
    "confidence": 0.85,  # 0-1 scale
    "reasoning": "Detailed explanation of the trade signal..."
}
```

## Data Leakage Protection

The agent enforces strict time-awareness:

```python
# ✓ Valid: Historical timestamp (assumed Eastern Time if naive)
signals = agent.run(news, timestamp=datetime(2024, 10, 1, 14, 0))

# ✓ Valid: Timezone-aware timestamp (converted to Eastern Time)
import pytz
utc_time = datetime(2024, 10, 1, 18, 0, tzinfo=pytz.UTC)
signals = agent.run(news, timestamp=utc_time)  # Converted to 2:00 PM ET

# ✗ Invalid: Future timestamp (raises ValueError)
signals = agent.run(news, timestamp=datetime(2026, 1, 1, 10, 0))
```

All database queries use `WHERE date < timestamp` to ensure no future data is accessed.

**Timezone Handling**: All timestamps are converted to US Eastern Time internally. See [TIMEZONE_GUIDE.md](TIMEZONE_GUIDE.md) for complete details.

## LangSmith Integration

Enable tracing for full observability:

```bash
# In .env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_key
LANGCHAIN_PROJECT=news-trading-agent
```

View traces at: https://smith.langchain.com

You'll see:
- Full execution flow through all 12 nodes
- LLM prompts and responses
- Tool invocations
- Timing and performance metrics
- Token usage and costs

## Examples

See `example_usage.py` for comprehensive examples:

```bash
python news_agent/example_usage.py
```

Examples include:
1. Single news analysis
2. Batch processing
3. Custom configuration
4. Data leakage protection demo
5. Context manager usage

## Tools

### 1. Ticker Extraction
- Uses LLM to identify tickers from news
- Validates with Yahoo Finance API
- Returns relevance scores

### 2. Database Query
- Time-aware historical price data
- Prevents data leakage
- Connection pooling

### 3. Technical Analysis
- ATR, RSI, Moving Averages
- Bollinger Bands
- Support/Resistance levels

### 4. Sentiment Analysis
- LLM-based sentiment (bullish/bearish/neutral)
- Intensity scoring (0-100)
- Per-ticker sentiment

### 5. Sector/Competitor Analysis
- Yahoo Finance sector data
- Competitor identification
- Sector trend analysis

### 6. Historical Pattern Matching
- Vector embeddings (text-embedding-004)
- Similarity search in PostgreSQL
- 40-minute price impact measurement
- Market hours filtering

### 7. Risk Assessment
- Uses volatility_volume_detector_tool
- Multi-factor risk scoring
- Hot stock detection

### 8. Volatility/Volume Detector
- Detects high volatility in past day
- Identifies volume spikes (2x+ average)
- Determines if stock is "hot" from previous news

### 9. Get News by ID
- Retrieves historical news articles
- Market hours validation
- Price impact calculation

### 10. Get Recent News
- Fetches news from last X hours
- Ticker-specific filtering
- Time-aware queries

## Database Schema

Required tables:

### Historical Price Data
```sql
-- One table per ticker (e.g., historical_data_aapl)
CREATE TABLE historical_data_{ticker} (
    date TIMESTAMP PRIMARY KEY,
    open FLOAT,
    high FLOAT,
    low FLOAT,
    close FLOAT,
    volume BIGINT,
    fetch_time TIMESTAMP
);
```

### News Data (with vector embeddings)
```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE bloomberg_news (
    id SERIAL PRIMARY KEY,
    link TEXT UNIQUE,
    published TIMESTAMP,
    fetch_time TIMESTAMP,
    title TEXT,
    summary TEXT,
    full_text TEXT,
    embedding vector(768),  -- For similarity search
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for similarity search
CREATE INDEX bloomberg_news_embedding_idx 
ON bloomberg_news 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

## Performance

Typical analysis time per news article:
- 1-2 tickers: 10-15 seconds
- 3-5 tickers: 20-30 seconds
- Factors: LLM latency, database queries, technical calculations

Optimize by:
- Using database connection pooling
- Caching technical indicators
- Batching similar calculations

## Troubleshooting

### No signals generated
- Check confidence threshold (try lowering it)
- Verify historical data exists for identified tickers
- Check LangSmith traces for details

### Database connection errors
- Verify database credentials in .env
- Check database accessibility
- Ensure tables exist for tickers

### Ticker not found
- Verify ticker symbol is correct
- Check if historical_data_{ticker} table exists
- Ensure data is available for the timestamp

### LangSmith not working
- Verify LANGCHAIN_API_KEY is set
- Check LANGCHAIN_TRACING_V2=true
- Confirm project name is correct

## Testing

Run tests with historical data:

```bash
# Basic functionality test
python -c "
from news_agent import TradingAgent
from datetime import datetime

with TradingAgent() as agent:
    signals = agent.run(
        news_text='Apple reports strong earnings',
        timestamp=datetime(2024, 10, 1, 10, 0)
    )
    print(f'Generated {len(signals)} signals')
"
```

## License

See main project LICENSE

## Support

For issues, questions, or contributions, see the main project repository.



