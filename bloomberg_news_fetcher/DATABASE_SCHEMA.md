# Trade Agent Database Schema

## Database: `trade_agent`
**Host**: YOUR_DB_HOST  
**Port**: 5432  
**User**: admin

---

## Tables Overview

| Table Name | Rows | Size | Indexes Size | Description |
|------------|------|------|--------------|-------------|
| **news** | 69 | 48 KB | 12 MB | Bloomberg news articles with vector embeddings |
| **stock_prices** | 18,324 | 1.7 MB | 2.6 MB | 1-minute OHLC price data for US stocks |
| **news_parser_log** | 5,078 | 1.9 MB | 784 KB | Application logs and events |
| **TOTAL** | 23,471 | **3.6 MB** | **15.4 MB** | All tables combined |

---

## Table: `news`

Bloomberg technology news articles with full text and vector embeddings for semantic search.

### Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| **id** | INTEGER | NOT NULL | AUTO | Auto-incrementing ID for joins |
| **link** | TEXT | NOT NULL | - | Article URL (PRIMARY KEY) |
| published | TIMESTAMP | YES | - | Original publication date from RSS |
| fetch_time | TIMESTAMP | YES | - | When article was fetched (Moscow time) |
| title | TEXT | YES | - | Article title |
| summary | TEXT | YES | - | Article summary from RSS feed |
| full_text | TEXT | YES | - | Full article text (scraped) |
| created_at | TIMESTAMP | YES | CURRENT_TIMESTAMP | Record creation time |
| source | VARCHAR(50) | YES | - | News source (default: 'bloomberg') |
| **title_summary_vector** | JSONB | YES | - | 768-dim embedding (title + summary) |
| **full_text_vector** | JSONB | YES | - | 768-dim embedding (full text) |

### Indexes

| Index Name | Type | Columns | Description |
|------------|------|---------|-------------|
| `bloomberg_news_pkey` | PRIMARY KEY (BTREE) | link | Primary key constraint |
| `bloomberg_news_id_key` | UNIQUE (BTREE) | id | Unique ID for joins |
| `idx_news_published` | BTREE | published DESC | Fast date queries |
| `idx_news_fetch_time` | BTREE | fetch_time DESC | Fast fetch time queries |
| `idx_news_source` | BTREE | source | Filter by source |
| `idx_news_title_summary_vector` | GIN | title_summary_vector | Fast vector searches |
| `idx_news_full_text_vector` | GIN | full_text_vector | Fast vector searches |

### Statistics

- **Total Articles**: 69
- **Date Range**: 2025-11-04 to 2025-11-08
- **Unique Sources**: 1 (bloomberg)
- **Vector Model**: Vertex AI `text-embedding-004` (768 dimensions)

### Sample Query

```sql
-- Get latest 10 articles
SELECT title, published, source 
FROM news 
ORDER BY published DESC 
LIMIT 10;

-- Search by source
SELECT COUNT(*) 
FROM news 
WHERE source = 'bloomberg';
```

---

## Table: `stock_prices`

High-resolution 1-minute OHLC price data for 120 major US market tickers.

### Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| **id** | INTEGER | NOT NULL | AUTO | Auto-incrementing ID |
| **ticker** | VARCHAR(10) | NOT NULL | - | Stock ticker symbol |
| **timestamp** | TIMESTAMP | NOT NULL | - | Price timestamp (1-minute interval) |
| open | DECIMAL(12,4) | YES | - | Opening price |
| high | DECIMAL(12,4) | YES | - | High price |
| low | DECIMAL(12,4) | YES | - | Low price |
| close | DECIMAL(12,4) | YES | - | Close price |
| volume | BIGINT | YES | - | Trading volume |
| fetch_time | TIMESTAMP | YES | CURRENT_TIMESTAMP | When data was fetched |

### Indexes

| Index Name | Type | Columns | Description |
|------------|------|---------|-------------|
| `stock_prices_pkey` | PRIMARY KEY (BTREE) | id | Primary key |
| `stock_prices_ticker_timestamp_key` | UNIQUE (BTREE) | ticker, timestamp | Prevent duplicates |
| `idx_stock_prices_ticker` | BTREE | ticker | Fast ticker lookups |
| `idx_stock_prices_timestamp` | BTREE | timestamp DESC | Fast time queries |
| `idx_stock_prices_ticker_timestamp` | BTREE | ticker, timestamp DESC | Combined queries |
| `idx_stock_prices_fetch_time` | BTREE | fetch_time DESC | Track fetch history |

### Statistics

- **Total Records**: 18,324
- **Unique Tickers**: 47 (out of 120 in system)
- **Date Range**: 2025-11-07 (1 trading day)
- **Data Points per Ticker**: ~390 per day (6.5 trading hours × 60 minutes)

### Sample Queries

```sql
-- Latest price for AAPL
SELECT * FROM stock_prices 
WHERE ticker = 'AAPL' 
ORDER BY timestamp DESC 
LIMIT 1;

-- Price range for today
SELECT 
    ticker,
    MIN(low) as day_low,
    MAX(high) as day_high,
    (SELECT close FROM stock_prices sp2 
     WHERE sp2.ticker = sp.ticker 
     ORDER BY timestamp DESC LIMIT 1) as current
FROM stock_prices sp
WHERE timestamp >= CURRENT_DATE
GROUP BY ticker;

-- All tickers with data
SELECT DISTINCT ticker 
FROM stock_prices 
ORDER BY ticker;
```

---

## Table: `news_parser_log`

Application logs for news fetcher and price fetcher operations. Auto-limited to 5000 most recent entries.

### Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| **id** | INTEGER | NOT NULL | AUTO | Auto-incrementing log entry ID |
| log_time | TIMESTAMP | YES | CURRENT_TIMESTAMP | Timestamp of log entry |
| log_level | VARCHAR(20) | YES | - | INFO, WARNING, ERROR |
| event_type | VARCHAR(50) | YES | - | Event type code |
| message | TEXT | YES | - | Log message |
| article_link | TEXT | YES | - | Article URL (if applicable) |
| details | JSONB | YES | - | Additional details in JSON |

### Indexes

| Index Name | Type | Columns | Description |
|------------|------|---------|-------------|
| `news_parser_log_pkey` | PRIMARY KEY (BTREE) | id | Primary key |
| `idx_news_parser_log_time` | BTREE | log_time DESC | Fast time queries |
| `idx_news_parser_log_level` | BTREE | log_level | Filter by level |
| `idx_news_parser_log_event` | BTREE | event_type | Filter by event |

### Event Types

#### News Fetcher Events:
- `FETCH_START` - News fetch operation started
- `FETCH_COMPLETE` - News fetch completed
- `FETCH_EMPTY` - No articles found
- `ARTICLE_FETCHED` - Successfully fetched article
- `ARTICLE_PARTIAL` - Article fetched without full text
- `DB_SAVE` - Article saved to database
- `DB_SKIP` - Article skipped (already exists)
- `DB_ERROR` - Database error

#### Price Fetcher Events:
- `PRICE_FETCH_START` - Price fetch started
- `PRICE_FETCH_SUCCESS` - Ticker data fetched
- `PRICE_FETCH_ERROR` - Error fetching ticker
- `PRICE_DB_ERROR` - Database error
- `PRICE_FETCH_COMPLETE` - Batch completed

### Statistics

- **Total Logs**: 5,078 (auto-limited to 5,000 most recent)
- **Date Range**: 2025-11-08 (current day)
- **Unique Event Types**: 9
- **Log Levels**: INFO, WARNING, ERROR

### Sample Queries

```sql
-- Recent errors
SELECT log_time, event_type, message 
FROM news_parser_log 
WHERE log_level = 'ERROR' 
ORDER BY log_time DESC 
LIMIT 10;

-- Fetch statistics for last 24 hours
SELECT 
    event_type,
    COUNT(*) as count
FROM news_parser_log
WHERE log_time >= NOW() - INTERVAL '24 hours'
GROUP BY event_type
ORDER BY count DESC;

-- View logs with script
-- python view_logs.py
```

---

## Database Relationships

```
┌─────────────────────────────────────────────────────────┐
│                    trade_agent DB                       │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────────┐                                   │
│  │      news        │  69 articles                       │
│  ├──────────────────┤                                   │
│  │ id (UNIQUE)      │◄─────────┐ For joins              │
│  │ link (PK)        │          │                        │
│  │ published        │          │                        │
│  │ title            │          │                        │
│  │ full_text        │          │                        │
│  │ *_vector (768d)  │          │                        │
│  └──────────────────┘          │                        │
│                                 │                        │
│  ┌──────────────────┐          │                        │
│  │  stock_prices    │  18,324 rows                      │
│  ├──────────────────┤          │                        │
│  │ id (PK)          │          │                        │
│  │ ticker+timestamp │◄─────────┤ Can correlate with    │
│  │ (UNIQUE)         │          │ news.published        │
│  │ ohlc, volume     │          │                        │
│  └──────────────────┘          │                        │
│                                 │                        │
│  ┌──────────────────┐          │                        │
│  │ news_parser_log  │  5,078 entries                    │
│  ├──────────────────┤          │                        │
│  │ id (PK)          │          │                        │
│  │ event_type       │──────────┘ References             │
│  │ article_link     │            news.link              │
│  │ details (JSON)   │                                   │
│  └──────────────────┘                                   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## Storage Statistics

### Current Usage
- **Total Database Size**: ~19 MB
- **Data Size**: 3.6 MB
- **Index Size**: 15.4 MB
- **Index/Data Ratio**: 4.3:1 (high due to vector embeddings)

### Projections

#### News Table (50 articles/day):
- **Daily**: ~50 KB data + ~800 KB indexes
- **Monthly**: ~1.5 MB data + ~24 MB indexes
- **Yearly**: ~18 MB data + ~288 MB indexes

#### Stock Prices (120 tickers, 1-day/run, every 5 min):
- **Per Run**: ~1.7 MB data + ~2.6 MB indexes
- **Daily** (288 runs): ~490 MB (excessive, needs optimization)
- **Recommended**: Run once per day or filter to recent data

#### Logs (auto-limited to 5,000):
- **Stable**: ~2-3 MB (auto-cleanup maintains limit)

---

## Data Quality & Constraints

### Duplicate Prevention
1. **news.link**: PRIMARY KEY - no duplicate articles
2. **stock_prices.(ticker, timestamp)**: UNIQUE - no duplicate price points
3. **news_parser_log**: Auto-cleanup keeps newest 5,000 entries

### Data Integrity
- All timestamps stored timezone-naive (UTC implicit)
- Decimal precision: 4 decimal places for prices
- Vector embeddings: 768 dimensions (Vertex AI standard)

### Performance Optimization
- GIN indexes on JSONB vector fields for fast similarity search
- Composite index on (ticker, timestamp) for time-series queries
- Descending indexes for latest-first queries

---

## Maintenance Queries

### Check Database Size
```sql
SELECT pg_size_pretty(pg_database_size('trade_agent'));
```

### Vacuum and Analyze
```sql
VACUUM ANALYZE news;
VACUUM ANALYZE stock_prices;
VACUUM ANALYZE news_parser_log;
```

### Index Usage Statistics
```sql
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;
```

### Table Bloat Check
```sql
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size,
    n_live_tup as live_tuples,
    n_dead_tup as dead_tuples
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC;
```

---

## Backup & Recovery

### Backup Commands
```bash
# Full database backup
pg_dump -h YOUR_DB_HOST -U admin -d trade_agent > backup_$(date +%Y%m%d).sql

# Table-specific backup
pg_dump -h YOUR_DB_HOST -U admin -d trade_agent -t news > news_backup.sql

# Data-only (no schema)
pg_dump -h YOUR_DB_HOST -U admin -d trade_agent --data-only > data_backup.sql
```

### Restore Commands
```bash
# Restore full database
psql -h YOUR_DB_HOST -U admin -d trade_agent < backup.sql

# Restore specific table
psql -h YOUR_DB_HOST -U admin -d trade_agent < news_backup.sql
```

---

## Connection Information

```python
# Python (psycopg2)
import psycopg2

conn = psycopg2.connect(
    host="YOUR_DB_HOST",
    database="trade_agent",
    user="admin",
    password="YOUR_DB_PASSWORD",
    port="5432"
)
```

```bash
# psql command line
psql -h YOUR_DB_HOST -U admin -d trade_agent

# With password in environment
export PGPASSWORD='YOUR_DB_PASSWORD'
psql -h YOUR_DB_HOST -U admin -d trade_agent
```

---

**Last Updated**: 2025-11-08  
**Schema Version**: 2.0 (with stock prices and vector embeddings)

