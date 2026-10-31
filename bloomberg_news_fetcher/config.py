"""
Bloomberg News Fetcher Configuration - loads from .env file
"""
import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    # Try current directory first
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # Try parent directory
        root_env = Path(__file__).parent.parent / '.env'
        if root_env.exists():
            load_dotenv(root_env)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist

# Database configuration - from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "trade_agent"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432")
}

# Bloomberg RSS feed categories
# 'opinion' was removed 2026-10-30. Its feed returns 404, and it never stored a single row,
# only a FETCH_ERROR every cycle. Bloomberg Opinion now lives at /bview/, but it is
# deliberately not fetched: the news_api consumers drop opinion pieces anyway.
BLOOMBERG_CATEGORIES = [
    'technology',
    'economics',
    'industries',
    'markets'
]

# Base URL for Bloomberg RSS feeds
BLOOMBERG_RSS_BASE_URL = "https://feeds.bloomberg.com"

# Legacy: Single RSS feed URL (for backward compatibility)
RSS_FEED_URL = f"{BLOOMBERG_RSS_BASE_URL}/technology/news.rss"

# External RSS feeds (CNBC + MarketWatch)
# Each feed has its own `source` label so DB rows are split by topic (same idea as Bloomberg categories).
EXTERNAL_RSS_URLS = [
    {"source": "cnbc-top", "rss_url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},        # CNBC Top News
    {"source": "cnbc-technology", "rss_url": "https://www.cnbc.com/id/19854910/device/rss/rss.html"}, # CNBC Technology
    {"source": "cnbc-finance", "rss_url": "https://www.cnbc.com/id/10000664/device/rss/rss.html"},     # CNBC Finance
    {"source": "cnbc-earnings", "rss_url": "https://www.cnbc.com/id/15839135/device/rss/rss.html"},    # CNBC Earnings
    {"source": "cnbc-economy", "rss_url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},      # CNBC Economy
    {"source": "cnbc-energy", "rss_url": "https://www.cnbc.com/id/19836768/device/rss/rss.html"},      # CNBC Energy
    {"source": "marketwatch-top", "rss_url": "http://feeds.marketwatch.com/marketwatch/topstories/"}, # MarketWatch Top
]
ENABLE_EXTERNAL_RSS = os.getenv("ENABLE_EXTERNAL_RSS", "true").lower() == "true"

# Fetch interval in minutes (default: 5)
FETCH_INTERVAL_MIN = 5

# Console output configuration
VERBOSE = False  # Enable/disable console output (logging still active when False)

# Full text fetching (default: off — saves time and avoids scraping issues)
FETCH_FULL_TEXT = os.getenv("FETCH_FULL_TEXT", "false").lower() == "true"

# Fundamentals fetching configuration (long-term agent)
# Runs on a WEEKLY cadence (not every interval) — SEC/yfinance are rate-limited and
# statements only change quarterly. EDGAR gives true point-in-time history; yfinance
# snapshots build a forward PIT archive. See fundamentals_fetcher.py.
FETCH_FUNDAMENTALS = os.getenv("FETCH_FUNDAMENTALS", "false").lower() == "true"
FUNDAMENTALS_FETCH_WEEKDAY = os.getenv("FUNDAMENTALS_FETCH_WEEKDAY", "saturday").lower()
FUNDAMENTALS_FETCH_TIME = os.getenv("FUNDAMENTALS_FETCH_TIME", "03:00")  # ET, off-market

# Daily OHLCV bars — split OFF the weekly fundamentals job on 2026-07-28. Statements change
# quarterly and belong on a weekly cadence; PRICES do not. Two measured costs of the weekly
# cadence:
#   * a cohort opened Sunday had no SPY entry bar until the NEXT Saturday, so /open showed
#     no vs-SPY for half the book for up to a week (seen 2026-07-28: 6 of 12 positions);
#   * the 2026-07-24 NaN incident sat in the table for days because nothing re-read it.
# Daily + a 1-month window makes both self-healing: a bar the writer drops as non-finite
# (daily_price_fetcher.save_daily) is simply refetched the next evening, and any gap inside
# the window closes without a manual repair.
#
# Why 22:30 and not 20:30 (moved 2026-10-24): from the 2026-09-02 session on, every 20:30
# run got the SAME-DAY bar from Yahoo with a non-finite OHLC. save_daily rightly dropped it
# for all 132 tickers, so bar D first reached the table at D+1 20:30. fetch_time shows it
# for every one of the 36 sessions 09-02..10-22; from 07-29 to 09-01 the same 20:30 run had
# landed bar D the same evening. Yahoo republishes the day's bar after the 20:00 ET
# extended-hours close and serves it with NaN Open/High/Low/Close (Volume present) for a
# while: reported "highly reproducible" at 00:30 UTC (= 20:30 EDT, our slot exactly) and
# not at 01:30 UTC (yfinance issue #2895). 22:30 sits an hour past that.
# The RETRY pass is the second chance before the 07:00 ET grader (LT_GRADE_TIME): if the
# evening pass still met a NaN bar, the grader would otherwise run a session behind.
# Empty string disables it.
DAILY_BARS_FETCH_ENABLED = os.getenv("DAILY_BARS_FETCH_ENABLED", "true").lower() == "true"
DAILY_BARS_FETCH_TIME = os.getenv("DAILY_BARS_FETCH_TIME", "22:30")   # ET, after Yahoo republishes
DAILY_BARS_RETRY_TIME = os.getenv("DAILY_BARS_RETRY_TIME", "06:15")   # ET, before the 07:00 grader
DAILY_BARS_PERIOD = os.getenv("DAILY_BARS_PERIOD", "1mo")             # heal window

# Macro/regime series (Modification Plan Phase 14) — free PIT series (VIX, HY OAS, 10Y,
# oil, Fed liquidity) into `macro_series`, daily off-market. Values are collected and
# stamped onto signals as regime context; NOTHING gates on them yet (single-regime data).
MACRO_FETCH_ENABLED = os.getenv("MACRO_FETCH_ENABLED", "true").lower() == "true"
MACRO_FETCH_TIME = os.getenv("MACRO_FETCH_TIME", "18:30")  # ET — after FRED daily updates

# FMP free tier (consensus groundwork, 2026-07-15): per-ticker earnings history with
# revenue/EPS actual-vs-estimated + forward report consensus. Key absent → FMP snapshots
# are skipped silently (yfinance/EDGAR unaffected). Env name tolerated in either case.
FMP_API_KEY = os.getenv("FMP_API_KEY") or os.getenv("FMP_API_key", "")

# EDGAR filings (Plan 9.1/9.2): daily 8-K/press-release text + Form 4 insider parse.
# Off-market, after EDGAR's evening indexing settles. Incremental window heals misses.
FILINGS_FETCH_ENABLED = os.getenv("FILINGS_FETCH_ENABLED", "true").lower() == "true"
FILINGS_FETCH_TIME = os.getenv("FILINGS_FETCH_TIME", "19:30")   # ET
FILINGS_LOOKBACK_DAYS = int(os.getenv("FILINGS_LOOKBACK_DAYS", "7"))

# Options IV snapshots (Plan 9.4): forward-PIT ATM-IV archive; chains are ephemeral so
# this can never be backfilled — cadence IS the dataset. After close, before macro fetch.
OPTIONS_FETCH_ENABLED = os.getenv("OPTIONS_FETCH_ENABLED", "true").lower() == "true"
OPTIONS_FETCH_TIME = os.getenv("OPTIONS_FETCH_TIME", "17:30")   # ET

# Graph maintenance (Neo4j story lifecycle + pgvector retention)
# Without these, the news->graph fan-out grows unbounded: stale Stories are never
# closed, duplicate Stories never merged, and graph_event_embeddings has no TTL.
# Runs off-market on a daily/weekly cadence (mirrors the fundamentals schedule).
GRAPH_MAINTENANCE_ENABLED = os.getenv("GRAPH_MAINTENANCE_ENABLED", "true").lower() == "true"
GRAPH_CLEANUP_TIME = os.getenv("GRAPH_CLEANUP_TIME", "04:00")          # daily, ET (close stale + purge)
GRAPH_MERGE_WEEKDAY = os.getenv("GRAPH_MERGE_WEEKDAY", "sunday").lower()  # weekly dedup merge
GRAPH_MERGE_TIME = os.getenv("GRAPH_MERGE_TIME", "04:30")               # ET
# graph_event_embeddings retention in days (0 = keep all, disables the purge)
EVENT_EMBEDDING_RETENTION_DAYS = int(os.getenv("EVENT_EMBEDDING_RETENTION_DAYS", "365"))

# Startup DB maintenance — one-time disk-reclaim runbook applied at boot (drop
# legacy JSONB embedding columns + GIN indexes, drop redundant price indexes,
# tune autovacuum, reclaim heap). The pgvector migration is already complete, so
# this is OFF by default. Set DB_MAINTENANCE_ON_START=true to run the (idempotent)
# cleanup once more. See disk_cleanup_runbook.sql for the manual form.
DB_MAINTENANCE_ON_START = os.getenv("DB_MAINTENANCE_ON_START", "false").lower() == "true"
# VACUUM FULL holds a brief ACCESS EXCLUSIVE lock on `news` and needs ~1x free
# disk. It runs automatically the first time legacy columns are dropped; set this
# true to force it on a later boot.
DB_VACUUM_FULL_ON_START = os.getenv("DB_VACUUM_FULL_ON_START", "false").lower() == "true"

# Price fetching configuration
FETCH_PRICES = True  # Enable/disable price fetching
PRICE_FETCH_PERIOD = '1d'  # Period for price data: '1d', '5d', '1mo'
# Note: Data is fetched at 1-minute intervals (interval='1m')
#       Results in ~390 data points per ticker per day (6.5 trading hours)
#       Stored in per-ticker tables: historical_data_{ticker}

# Timezone: All timestamps stored in US Eastern Time (America/New_York)
# This is consistent with:
# - US market hours (9:30 AM - 4:00 PM ET)
# - Trading agent timezone
# - Most financial news timestamps
TIMEZONE = "America/New_York"

