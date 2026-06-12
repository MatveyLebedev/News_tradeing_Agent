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
# Available categories: technology, economics, industries, opinion, markets
BLOOMBERG_CATEGORIES = [
    'technology',
    'economics',
    'industries',
    'opinion',
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

