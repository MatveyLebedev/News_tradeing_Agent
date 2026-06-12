# Stock Price Fetcher

Automated system for fetching 1-minute historical price data for top US market tickers.

## Features

- **120 Major US Tickers**: Covers top companies across all sectors
- **1-Minute Intervals**: High-resolution price data
- **Automatic Deduplication**: Prevents duplicate entries
- **Database Logging**: Tracks all fetch operations and errors
- **Flexible Time Periods**: Supports 1d, 5d, 1mo periods
- **Integration with News Fetcher**: Runs on the same schedule

## Ticker Coverage

The price fetcher includes **120 tickers** from major US market indices (S&P 100, Russell Top 50):

### By Sector:
- **Technology**: AAPL, MSFT, GOOGL, GOOG, AMZN, NVDA, META, TSLA, etc. (30+ tickers)
- **Finance**: JPM, V, MA, BAC, WFC, GS, MS, BLK, etc. (13 tickers)
- **Healthcare**: UNH, JNJ, LLY, ABBV, MRK, TMO, PFE, etc. (19 tickers)
- **Consumer**: WMT, HD, COST, PG, KO, PEP, MCD, NKE, etc. (15 tickers)
- **Energy**: XOM, CVX, COP, SLB, EOG, PSX, MPC (7 tickers)
- **Industrials**: CAT, BA, HON, UNP, RTX, GE, LMT, etc. (11 tickers)
- **Media & Entertainment**: DIS, NFLX, CMCSA, T, VZ, etc. (7 tickers)
- **Consumer Services**: PYPL, ADP, UBER, SQ, ABNB (5 tickers)
- **Utilities**: NEE, DUK, SO, AEP (4 tickers)
- **Materials**: LIN, APD, SHW, ECL, NEM (5 tickers)
- **Real Estate**: PLD, AMT, CCI, EQIX, PSA (5 tickers)

## Database Schema

```sql
CREATE TABLE stock_prices (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open DECIMAL(12, 4),
    high DECIMAL(12, 4),
    low DECIMAL(12, 4),
    close DECIMAL(12, 4),
    volume BIGINT,
    fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, timestamp)
);
```

## Usage

### Manual Run

```bash
# Fetch 1 day of 1-minute data for all tickers (default)
python price_fetcher.py

# Fetch 5 days of data
python price_fetcher.py --period 5d

# Fetch 1 month of data
python price_fetcher.py --period 1mo

# Fetch specific tickers only
python price_fetcher.py --tickers AAPL MSFT GOOGL AMZN
```

### Automated Run (with News Fetcher)

The price fetcher runs automatically every 5 minutes when integrated with the news fetcher:

```bash
# Start the service
docker-compose up -d

# View logs
docker logs -f bloomberg_news_fetcher
```

## Configuration

Edit `config.py`:

```python
# Enable/disable price fetching
FETCH_PRICES = True

# Period for price data
PRICE_FETCH_PERIOD = '1d'  # Options: '1d', '5d', '1mo'
```

## Environment Variables

```bash
# Enable price fetching in Docker
FETCH_PRICES=true

# Fetch interval (applies to both news and prices)
FETCH_INTERVAL_MIN=5
```

## Data Volume Estimates

### Per Ticker:
- **1 day**: ~390 rows (1 per minute during market hours: 6.5 hours * 60 min)
- **5 days**: ~1,950 rows
- **1 month (~20 trading days)**: ~7,800 rows

### All Tickers (120):
- **1 day**: ~46,800 rows
- **5 days**: ~234,000 rows
- **1 month (~20 trading days)**: ~936,000 rows

### Storage:
- Each row: ~100 bytes
- 1 day for all tickers: ~4.9 MB
- 1 month for all tickers: ~98 MB
- 1 year for all tickers: ~1.2 GB

## Logging

All price fetch operations are logged to the `news_parser_log` table:

```bash
# View logs
python view_logs.py

# View only price-related logs
python view_logs.py | grep -i "price"
```

Log events:
- `PRICE_FETCH_START`: Fetch operation started
- `PRICE_FETCH_SUCCESS`: Successfully fetched data for ticker
- `PRICE_FETCH_ERROR`: Error fetching data for ticker
- `PRICE_DB_ERROR`: Error saving data to database
- `PRICE_FETCH_COMPLETE`: Fetch operation completed

## Data Quality

- **Real-time data**: yfinance provides near real-time data from Yahoo Finance
- **Deduplication**: Unique constraint on (ticker, timestamp) prevents duplicates
- **Error handling**: Failed tickers are logged but don't stop the batch
- **Retry logic**: Can be run multiple times safely (duplicates are skipped)

## Performance

- **Average fetch time**: ~2-5 seconds per ticker
- **Total time (120 tickers)**: ~4-10 minutes for 1-day data
- **Concurrent requests**: Sequential to avoid API rate limits
- **Database writes**: Bulk inserts with conflict handling

## Querying Price Data

```sql
-- Get latest price for a ticker
SELECT * FROM stock_prices 
WHERE ticker = 'AAPL' 
ORDER BY timestamp DESC 
LIMIT 1;

-- Get price range for today
SELECT ticker, 
       MIN(low) as day_low, 
       MAX(high) as day_high,
       LAST_VALUE(close) as current_price
FROM stock_prices 
WHERE ticker = 'AAPL' 
  AND timestamp >= CURRENT_DATE
GROUP BY ticker;

-- Get all tickers with data
SELECT DISTINCT ticker 
FROM stock_prices 
ORDER BY ticker;

-- Count rows per ticker
SELECT ticker, COUNT(*) as row_count 
FROM stock_prices 
GROUP BY ticker 
ORDER BY row_count DESC;
```

## Troubleshooting

### No data for some tickers
- Check if ticker symbol is correct
- Some tickers may not have 1-minute data available
- Market hours: data only available during trading hours (9:30 AM - 4:00 PM ET)

### Slow fetching
- Reduce number of tickers
- Use shorter time period (1d instead of 1mo)
- Check network connection

### Database errors
- Verify database credentials in `config.py`
- Check database connection: `psql -h YOUR_DB_HOST -U admin -d trade_agent`
- Ensure table exists: run `create_price_table.sql`

## Integration with News Analysis

Price data can be correlated with news events:

```sql
-- Find news published near significant price movements
SELECT n.title, n.published, s.ticker, s.close
FROM news n
CROSS JOIN stock_prices s
WHERE s.timestamp >= n.published - INTERVAL '1 hour'
  AND s.timestamp <= n.published + INTERVAL '1 hour'
ORDER BY n.published DESC;
```

## Dependencies

- `yfinance>=0.2.40`: Yahoo Finance API for stock data
- `psycopg2-binary`: PostgreSQL adapter
- `pytz`: Timezone handling

