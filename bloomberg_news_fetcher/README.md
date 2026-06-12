# Bloomberg News Fetcher

Automated Bloomberg technology news fetcher that runs every 5 minutes, fetches articles via RSS, attempts to extract full text, and saves to PostgreSQL database.

## Features

- Fetches Bloomberg technology news from RSS feed
- Attempts to extract full article text (1 retry)
- Saves to PostgreSQL database (`trade_agent.news`)
- **Generates vector embeddings** for semantic search (Vertex AI)
  - Title + Summary vector (768 dimensions)
  - Full text vector (768 dimensions)
- Includes source field (currently "bloomberg")
- Runs on configurable interval (default: 5 minutes)
- Avoids duplicates using unique constraint on article URL
- Docker support for easy deployment

## Database Setup

**For new installation:**

```bash
psql -h YOUR_DB_HOST -U admin -d trade_agent -f create_table.sql
```

**If upgrading from old `bloomberg_news` table:**

```bash
psql -h YOUR_DB_HOST -U admin -d trade_agent -f migrate_table.sql
```

Or connect manually and run the SQL in `create_table.sql` or `migrate_table.sql`.

## Configuration

Edit `config.py` to change:
- Database connection settings
- RSS feed URL
- Default fetch interval
- Console output verbosity

Environment variables (Docker):
- `FETCH_INTERVAL_MIN` - Fetch interval in minutes (default: 5)
- `VERBOSE` - Enable/disable console output (default: true, logging still active when false)
- `FETCH_PRICES` - Enable/disable price fetching (default: true)
- `TZ` - Timezone (default: America/New_York)

## Running Locally

### Install Dependencies

```bash
cd bloomberg_news_fetcher
pip install -r requirements.txt
```

### Run Options

**Start scheduler (runs every 5 minutes):**
```bash
python auto_news_fetcher.py
```

**Fetch immediately then start scheduler:**
```bash
python auto_news_fetcher.py --now
```

**Fetch once and exit:**
```bash
python auto_news_fetcher.py --once
```

**Custom interval:**
```bash
python auto_news_fetcher.py --interval 10  # Every 10 minutes
```

**Quiet mode (suppress console output, logging still active):**
```bash
python auto_news_fetcher.py --quiet
```

**Verbose mode (force console output, overrides config):**
```bash
python auto_news_fetcher.py --verbose
```

## Running with Docker

### Build and run using docker-compose (from project root):

```bash
cd ..
docker-compose up -d news-fetcher
```

### View logs:

```bash
docker-compose logs -f news-fetcher
```

### Stop service:

```bash
docker-compose stop news-fetcher
```

### Rebuild after code changes:

```bash
docker-compose up -d --build news-fetcher
```

## Database Schema

The `news` table contains:

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Auto-incrementing ID for joins |
| link | TEXT | Article URL (primary key) |
| source | VARCHAR(50) | News source (e.g., "bloomberg") |
| published | TIMESTAMP | Original publication date |
| fetch_time | TIMESTAMP | When article was fetched (Moscow time) |
| title | TEXT | Article title |
| summary | TEXT | RSS summary |
| full_text | TEXT | Full article text (if available) |
| title_summary_vector | JSONB | Vector embedding of title+summary (768 dims) |
| full_text_vector | JSONB | Vector embedding of full text (768 dims) |
| created_at | TIMESTAMP | Row creation timestamp |

## Architecture

- `config.py` - Configuration settings
- `news_fetcher.py` - RSS parsing and database operations
- `auto_news_fetcher.py` - Scheduler and CLI interface
- `embeddings.py` - Vector embedding generation using Vertex AI
- `search_similar_news.py` - Semantic similarity search
- `logger.py` - Database logging with 5000 record limit
- `view_logs.py` - Script to view and analyze logs
- `db_report.py` - Script to generate database report
- `Dockerfile.news` - Container definition
- `create_table.sql` - Database schema for news articles
- `add_vector_columns.sql` - Add vector columns to existing table
- `create_log_table.sql` - Database schema for logs
- `migrate_table.sql` - Migration script from old schema

## Logging

All fetch operations, errors, and events are logged to the `news_parser_log` table.

### View Logs

**Show recent logs:**
```bash
python view_logs.py
```

**Show last 100 logs:**
```bash
python view_logs.py --limit 100
```

**Show only errors:**
```bash
python view_logs.py --level ERROR
```

**Show logs from last 24 hours:**
```bash
python view_logs.py --hours 24
```

**Filter by event type:**
```bash
python view_logs.py --event FETCH_ERROR
```

**View statistics:**
```bash
python view_logs.py --stats
```

Statistics include:
- **Last 24 Hours**: Articles fetched, skipped, errors, fetch operations
- **All Time**: Total logs, breakdown by level and event type

### Log Event Types

- `FETCH_START` - Fetch operation started
- `FETCH_COMPLETE` - Fetch operation completed
- `FETCH_ERROR` - Error fetching RSS feed
- `FETCH_EMPTY` - No articles found in feed
- `ARTICLE_FETCHED` - Successfully fetched full text for article
- `ARTICLE_PARTIAL` - Partial or no full text available
- `DB_SAVE` - Article saved to database
- `DB_SKIP` - Article already exists, skipped
- `DB_ERROR` - Database error

The log table automatically maintains a maximum of 5000 records by deleting older entries.

## Semantic Search

Search for similar articles using vector embeddings:

**Basic search:**
```bash
python search_similar_news.py "artificial intelligence and machine learning"
```

**Search with more results:**
```bash
python search_similar_news.py "cryptocurrency regulation" --top 10
```

**Search using full text vectors:**
```bash
python search_similar_news.py "cloud computing trends" --full-text
```

The search uses cosine similarity to find articles with similar semantic meaning, not just keyword matches.

## Database Report

Generate a comprehensive database report showing all tables, structures, indexes, and data:

```bash
python db_report.py
```

This will show:
- Database size
- All tables with sizes
- Table structures (columns, types, constraints)
- All indexes
- Recent records from each table

## Vector Embeddings

The system automatically generates two types of embeddings for each article:

1. **Title + Summary Vector** (768 dimensions)
   - Used for quick semantic search
   - Captures main topic and key points

2. **Full Text Vector** (768 dimensions)
   - Used for deep semantic search
   - Captures full article context

Embeddings are generated using **Vertex AI text-embedding-004** model with **768 dimensions** (maximum supported) and stored as JSONB arrays in PostgreSQL.

**Note**: The model supports dimensions from 1-768. We use 768 for maximum accuracy and semantic understanding.

## Notes

- Full text extraction attempts to scrape article content (1 retry only)
- Some articles may be paywalled and return limited content
- **Duplicate detection**: Before processing, the system checks if article URL already exists in database
  - If exists: Skips vector generation and database write (saves API calls and time)
  - If new: Generates embeddings and saves to database
- `link` is the primary key, `id` is auto-incrementing for joins with other tables
- `fetch_time` is stored in Moscow timezone (Europe/Moscow)
- Scheduler runs continuously and must be kept running

