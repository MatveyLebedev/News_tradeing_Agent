import schedule
import time
from datetime import datetime
import pytz
import os
import psycopg2
from news_fetcher import fetch_and_save_news, embedding_generator, logger
from config import BLOOMBERG_CATEGORIES, ENABLE_EXTERNAL_RSS
from price_fetcher import fetch_all_tickers, TOP_50_TICKERS
from config import FETCH_PRICES, PRICE_FETCH_PERIOD, VERBOSE, FETCH_FULL_TEXT
from config import FETCH_FUNDAMENTALS, FUNDAMENTALS_FETCH_WEEKDAY, FUNDAMENTALS_FETCH_TIME
from graph_db import update_price_movements

# US Eastern Time zone
ET_TIMEZONE = pytz.timezone('America/New_York')


def _test_embedding_api():
    """Verify embedding API works at startup and log result to DB."""
    try:
        vec = embedding_generator.generate_embedding("startup connectivity test")
        if vec and len(vec) > 0:
            logger.info('EMBEDDING_TEST', f"Embedding API OK: {len(vec)} dims")
            print(f"✓ Embedding API OK ({len(vec)} dims)")
        else:
            logger.error('EMBEDDING_TEST', "Embedding API returned None (check OPENROUTER_API_KEY)")
            print("✗ Embedding API returned None")
    except Exception as e:
        logger.error('EMBEDDING_TEST', f"Embedding API FAILED: {e}",
                     details={'error': str(e)})
        print(f"✗ Embedding API FAILED: {e}")


def _init_graph_tables():
    """Ensure graph support tables exist in postgres before first fetch."""
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"), database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
            port=int(os.getenv("DB_PORT", 5432)),
        )
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS graph_event_embeddings (
                    event_id TEXT PRIMARY KEY,
                    embedding vector(1536),
                    event_date DATE
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS gee_date_idx ON graph_event_embeddings (event_date)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS graph_story_centroids (
                    story_key TEXT PRIMARY KEY,
                    centroid vector(1536),
                    event_count INT DEFAULT 1,
                    last_updated TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
        conn.close()
        print("✓ Graph tables ready")
    except Exception as e:
        print(f"⚠️  Could not init graph tables: {e}")

# Configuration - read from environment variables or use defaults
FETCH_INTERVAL_MIN = int(os.getenv("FETCH_INTERVAL_MIN", "5"))
FETCH_PRICES_ENABLED = os.getenv("FETCH_PRICES", str(FETCH_PRICES)).lower() == 'true'
FETCH_FULL_TEXT_ENABLED = os.getenv("FETCH_FULL_TEXT", str(FETCH_FULL_TEXT)).lower() == 'true'
VERBOSE_ENABLED = os.getenv("VERBOSE", str(VERBOSE)).lower() == 'true'


def scheduled_fundamentals_fetch(verbose=True):
    """Weekly heavy-data fetch: daily price bars + fundamentals (EDGAR PIT + yfinance)."""
    if verbose:
        print("\n" + "=" * 80)
        print(f"WEEKLY FUNDAMENTALS + DAILY PRICE FETCH - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    # Imported lazily so a missing dep here never blocks the news/price loop.
    try:
        from daily_price_fetcher import fetch_all_daily
        # 5d top-up keeps daily bars current; the initial 2y backfill is run manually once.
        dr = fetch_all_daily(tickers=TOP_50_TICKERS, period="5d", verbose=verbose)
        if verbose:
            print(f"✓ Daily prices: {dr['rows']} rows")
    except Exception as e:
        print(f"❌ Daily price fetch failed: {e}")
    try:
        from fundamentals_fetcher import fetch_fundamentals
        result = fetch_fundamentals(tickers=TOP_50_TICKERS, verbose=verbose)
        if verbose:
            print(f"✓ Fundamentals: {result['edgar_rows']} EDGAR rows, {result['yf_rows']} yfinance rows")
    except Exception as e:
        print(f"❌ Fundamentals fetch failed: {e}")


def scheduled_fetch(verbose=True):
    """
    Fetch news and prices, save to database
    This runs at the configured interval
    
    Args:
        verbose: If False, suppresses console output from fetchers
    """
    et_now = datetime.now(ET_TIMEZONE)
    
    if verbose:
        print("\n" + "=" * 80)
        print(f"AUTOMATED FETCH - {et_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    
    try:
        # Fetch news
        news_result = fetch_and_save_news(fetch_full_text=FETCH_FULL_TEXT_ENABLED, verbose=verbose)
        
        # Fetch prices if enabled
        price_result = None
        if FETCH_PRICES_ENABLED:
            # Price fetching logs are suppressed (verbose=False) to reduce noise
            price_result = fetch_all_tickers(tickers=TOP_50_TICKERS, period=PRICE_FETCH_PERIOD, verbose=False)
            try:
                update_price_movements()
            except Exception as e:
                print(f"⚠ Graph price movement update failed: {e}")
        
        if verbose:
            print("\n" + "=" * 80)
            print("FETCH COMPLETE")
            print("=" * 80)
            print(f"✓ News articles saved: {news_result}")
            if price_result:
                print(f"✓ Price data rows inserted: {price_result['total_rows']} ({price_result['success']}/{len(TOP_50_TICKERS)} tickers)")
            print(f"Next fetch in: {FETCH_INTERVAL_MIN} minutes")
            print("=" * 80)
        
    except Exception as e:
        if verbose:
            print(f"\n❌ ERROR during fetch: {e}")
            print(f"Will retry in {FETCH_INTERVAL_MIN} minutes.")


def run_scheduler(verbose=True):
    """
    Run the scheduler that fetches news at configured intervals
    
    Args:
        verbose: If False, suppresses console output from fetchers
    """
    # Schedule the job
    schedule.every(FETCH_INTERVAL_MIN).minutes.do(lambda: scheduled_fetch(verbose=verbose))

    # Weekly fundamentals fetch (off-market), if enabled
    if FETCH_FUNDAMENTALS:
        try:
            day_job = getattr(schedule.every(), FUNDAMENTALS_FETCH_WEEKDAY)
            day_job.at(FUNDAMENTALS_FETCH_TIME).do(lambda: scheduled_fundamentals_fetch(verbose=verbose))
            print(f"✓ Fundamentals fetch scheduled: {FUNDAMENTALS_FETCH_WEEKDAY} at {FUNDAMENTALS_FETCH_TIME} ET")
        except Exception as e:
            print(f"⚠️  Could not schedule fundamentals fetch: {e}")

    et_now = datetime.now(ET_TIMEZONE)

    _init_graph_tables()
    _test_embedding_api()

    print("=" * 80)
    print("AUTOMATED NEWS FETCHER - STARTED")
    print("=" * 80)
    print(f"Current time: {et_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Schedule: Every {FETCH_INTERVAL_MIN} minutes")
    print(f"Categories: {', '.join(BLOOMBERG_CATEGORIES)}")
    if ENABLE_EXTERNAL_RSS:
        print("Additional RSS sources: CNBC + MarketWatch")
    print()
    print("📊 The scheduler will:")
    print(f"  1. Fetch Bloomberg news from {len(BLOOMBERG_CATEGORIES)} categories every {FETCH_INTERVAL_MIN} minutes")
    print(f"     Categories: {', '.join(BLOOMBERG_CATEGORIES)}")
    if ENABLE_EXTERNAL_RSS:
        print("  2. Fetch CNBC + MarketWatch RSS feeds")
        print("  3. Attempt to fetch full article text (1 retry)")
        print("  4. Save articles to database (trade_agent.news)")
        print("  5. Skip duplicate articles (based on link)")
    else:
        print("  2. Attempt to fetch full article text (1 retry)")
        print("  3. Save articles to database (trade_agent.news)")
        print("  4. Skip duplicate articles (based on link)")
    print()
    print("⚠️  Note: This script must keep running. Use Ctrl+C to stop.")
    print("=" * 80)
    
    # Get next scheduled run time
    next_run = schedule.next_run()
    if next_run:
        print(f"\n⏰ Next scheduled fetch: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\n🔄 Scheduler is running... Press Ctrl+C to stop\n")
    
    # Keep the script running
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds
    except KeyboardInterrupt:
        print("\n\n" + "=" * 80)
        print("SCHEDULER STOPPED")
        print("=" * 80)
        print("Automated fetching has been terminated.")


def run_now_and_schedule(verbose=True):
    """
    Run the fetch immediately, then start the scheduler
    Useful for testing
    
    Args:
        verbose: If False, suppresses console output from fetchers
    """
    print("🚀 Running immediate fetch before starting scheduler...\n")
    scheduled_fetch(verbose=verbose)
    print("\n" + "=" * 80)
    print("Starting scheduler for future runs...")
    print("=" * 80)
    time.sleep(2)
    run_scheduler(verbose=verbose)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Automated fetcher for Bloomberg technology news'
    )
    parser.add_argument(
        '--now', 
        action='store_true',
        help='Fetch news immediately before starting scheduler'
    )
    parser.add_argument(
        '--once', 
        action='store_true',
        help='Fetch news once and exit (no scheduling)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=FETCH_INTERVAL_MIN,
        help=f'Fetch interval in minutes (default: {FETCH_INTERVAL_MIN})'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress console output from fetchers (logging still active)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable console output (overrides config/env VERBOSE setting)'
    )
    
    args = parser.parse_args()
    
    # Update interval if specified
    if args.interval != FETCH_INTERVAL_MIN:
        FETCH_INTERVAL_MIN = args.interval
        print(f"Using custom interval: {FETCH_INTERVAL_MIN} minutes")
    
    # Determine verbose mode: 
    # 1. If --quiet is specified, verbose = False
    # 2. If --verbose is specified, verbose = True
    # 3. Otherwise, use config/env setting (VERBOSE_ENABLED)
    if args.quiet:
        verbose = False
    elif args.verbose:
        verbose = True
    else:
        verbose = VERBOSE_ENABLED
    
    if args.once:
        # Run once and exit
        scheduled_fetch(verbose=verbose)
    elif args.now:
        # Run now and then start scheduler
        run_now_and_schedule(verbose=verbose)
    else:
        # Default: fetch immediately then start scheduler
        run_now_and_schedule(verbose=verbose)

