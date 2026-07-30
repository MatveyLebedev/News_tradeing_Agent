import schedule
import time
from datetime import datetime
import pytz
import os
import psycopg2
from news_fetcher import fetch_and_save_news, embedding_generator, logger
from config import BLOOMBERG_CATEGORIES, ENABLE_EXTERNAL_RSS
from price_fetcher import fetch_all_tickers, DEFAULT_TICKERS, TOP_50_TICKERS
from config import FETCH_PRICES, PRICE_FETCH_PERIOD, VERBOSE, FETCH_FULL_TEXT
from config import (FETCH_FUNDAMENTALS, FUNDAMENTALS_FETCH_WEEKDAY, FUNDAMENTALS_FETCH_TIME,
                    DAILY_BARS_FETCH_ENABLED, DAILY_BARS_FETCH_TIME, DAILY_BARS_PERIOD)
from config import (
    GRAPH_MAINTENANCE_ENABLED, GRAPH_CLEANUP_TIME,
    GRAPH_MERGE_WEEKDAY, GRAPH_MERGE_TIME, EVENT_EMBEDDING_RETENTION_DAYS,
)
from config import MACRO_FETCH_ENABLED, MACRO_FETCH_TIME
from config import (
    FILINGS_FETCH_ENABLED, FILINGS_FETCH_TIME, FILINGS_LOOKBACK_DAYS,
    OPTIONS_FETCH_ENABLED, OPTIONS_FETCH_TIME,
)
from graph_db import (
    update_price_movements,
    close_stale_stories,
    merge_duplicate_stories,
    purge_old_event_embeddings,
)
from db_maintenance import run_startup_db_maintenance

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


def scheduled_daily_bars_fetch(verbose=True):
    """Daily OHLCV top-up (see config: DAILY_BARS_FETCH_TIME).

    Split off the weekly fundamentals job so prices track the market rather than the
    filing calendar — grading, /open's vs-SPY leg and the freshness preflight all read
    this table daily.
    """
    if verbose:
        print("\n" + "=" * 80)
        print(f"DAILY PRICE BAR FETCH - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    # Imported lazily so a missing dep here never blocks the news/price loop.
    try:
        from daily_price_fetcher import fetch_all_daily
        # No explicit tickers: the fetcher's default is TOP_50 + BENCHMARK (SPY). Passing
        # bare TOP_50_TICKERS here silently excluded the grading benchmark. The window is
        # deliberately wider than the cadence — that overlap is what heals a dropped or
        # missed bar without anyone noticing it was missing.
        dr = fetch_all_daily(period=DAILY_BARS_PERIOD, verbose=verbose)
        if verbose:
            print(f"✓ Daily prices: {dr['rows']} rows")
    except Exception as e:
        print(f"❌ Daily price fetch failed: {e}")


def scheduled_fundamentals_fetch(verbose=True):
    """Weekly heavy-data fetch: fundamentals (EDGAR PIT + yfinance snapshots).

    Daily price bars used to ride along here; they now have their own daily job
    (`scheduled_daily_bars_fetch`) because prices go stale on a market clock, not a
    filing one.
    """
    if verbose:
        print("\n" + "=" * 80)
        print(f"WEEKLY FUNDAMENTALS FETCH - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    try:
        from fundamentals_fetcher import fetch_fundamentals
        result = fetch_fundamentals(tickers=TOP_50_TICKERS, verbose=verbose)
        if verbose:
            print(f"✓ Fundamentals: {result['edgar_rows']} EDGAR rows, {result['yf_rows']} yfinance rows")
    except Exception as e:
        print(f"❌ Fundamentals fetch failed: {e}")


def scheduled_macro_fetch(verbose=True):
    """Daily macro/regime series fetch (Phase 14): VIX, HY OAS, 10Y, oil, Fed liquidity."""
    if verbose:
        print("\n" + "=" * 80)
        print(f"DAILY MACRO SERIES FETCH - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    # Imported lazily so a missing dep here never blocks the news/price loop.
    try:
        from macro_fetcher import fetch_all_macro
        fetch_all_macro(verbose=verbose)
    except Exception as e:
        print(f"❌ Macro series fetch failed: {e}")


def scheduled_filings_fetch(verbose=True):
    """Daily EDGAR fetch (9.1/9.2): 8-K/press-release text + Form 4 insider transactions."""
    if verbose:
        print("\n" + "=" * 80)
        print(f"DAILY EDGAR FILINGS FETCH - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    try:
        from filings_fetcher import fetch_filings
        fetch_filings(days=FILINGS_LOOKBACK_DAYS, verbose=verbose)
    except Exception as e:
        print(f"❌ Filings fetch failed: {e}")


def scheduled_options_fetch(verbose=True):
    """Daily options ATM-IV snapshot (9.4) — forward-PIT, cannot be backfilled."""
    if verbose:
        print("\n" + "=" * 80)
        print(f"DAILY OPTIONS IV SNAPSHOT - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    try:
        from options_fetcher import fetch_all_options
        fetch_all_options(verbose=verbose)
    except Exception as e:
        print(f"❌ Options IV fetch failed: {e}")


def scheduled_graph_cleanup(verbose=True):
    """Daily graph maintenance: close stale Stories + purge old event embeddings.

    Bounds the two main ongoing-disk drivers from the news->graph fan-out:
    unbounded Neo4j Story growth and the no-TTL graph_event_embeddings table.
    """
    if verbose:
        print("\n" + "=" * 80)
        print(f"DAILY GRAPH CLEANUP - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    try:
        close_stale_stories()
        if verbose:
            print("✓ Closed stale stories")
    except Exception as e:
        print(f"❌ close_stale_stories failed: {e}")
    try:
        deleted = purge_old_event_embeddings(EVENT_EMBEDDING_RETENTION_DAYS)
        if verbose:
            print(f"✓ Purged {deleted} event embeddings older than {EVENT_EMBEDDING_RETENTION_DAYS}d")
    except Exception as e:
        print(f"❌ purge_old_event_embeddings failed: {e}")


def scheduled_graph_merge(verbose=True):
    """Weekly graph maintenance: merge near-duplicate active Stories."""
    if verbose:
        print("\n" + "=" * 80)
        print(f"WEEKLY GRAPH MERGE - {datetime.now(ET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 80)
    try:
        merge_duplicate_stories()
        if verbose:
            print("✓ Merged duplicate stories")
    except Exception as e:
        print(f"❌ merge_duplicate_stories failed: {e}")


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
            # Price fetching logs are suppressed (verbose=False) to reduce noise.
            # DEFAULT_TICKERS, not TOP_50_TICKERS: the company list carries no benchmark,
            # and passing it here is exactly the bug daily_price_fetcher already documents
            # ("passing bare TOP_50_TICKERS froze SPY and blocked all outcome grading").
            # On the intraday side it was never noticed because no ETF minute table had
            # ever been created — so the whole benchmark leg of every intraday experiment
            # had to be synthesised from an equal-weight basket instead.
            price_result = fetch_all_tickers(tickers=DEFAULT_TICKERS, period=PRICE_FETCH_PERIOD, verbose=False)
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
                print(f"✓ Price data rows inserted: {price_result['total_rows']} ({price_result['success']}/{len(DEFAULT_TICKERS)} tickers)")
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

    # Daily OHLCV top-up — scheduled BEFORE the weekly fundamentals job on purpose: prices
    # are what grading, /open and the batch freshness preflight all depend on, and they
    # must not be gated behind FETCH_FUNDAMENTALS.
    if DAILY_BARS_FETCH_ENABLED:
        try:
            schedule.every().day.at(DAILY_BARS_FETCH_TIME).do(
                lambda: scheduled_daily_bars_fetch(verbose=verbose))
            print(f"✓ Daily price bars scheduled: daily at {DAILY_BARS_FETCH_TIME} ET "
                  f"(period={DAILY_BARS_PERIOD})")
        except Exception as e:
            print(f"⚠️  Could not schedule daily price bars: {e}")

    # Weekly fundamentals fetch (off-market), if enabled
    if FETCH_FUNDAMENTALS:
        try:
            day_job = getattr(schedule.every(), FUNDAMENTALS_FETCH_WEEKDAY)
            day_job.at(FUNDAMENTALS_FETCH_TIME).do(lambda: scheduled_fundamentals_fetch(verbose=verbose))
            print(f"✓ Fundamentals fetch scheduled: {FUNDAMENTALS_FETCH_WEEKDAY} at {FUNDAMENTALS_FETCH_TIME} ET")
        except Exception as e:
            print(f"⚠️  Could not schedule fundamentals fetch: {e}")

    # Daily macro/regime series (Phase 14) — collect-and-stamp only, nothing gates on it.
    if MACRO_FETCH_ENABLED:
        try:
            schedule.every().day.at(MACRO_FETCH_TIME).do(lambda: scheduled_macro_fetch(verbose=verbose))
            print(f"✓ Macro series fetch scheduled: daily at {MACRO_FETCH_TIME} ET")
        except Exception as e:
            print(f"⚠️  Could not schedule macro fetch: {e}")

    # Daily EDGAR filings + insider (9.1/9.2) and options IV snapshot (9.4) — advisory data.
    if FILINGS_FETCH_ENABLED:
        try:
            schedule.every().day.at(FILINGS_FETCH_TIME).do(lambda: scheduled_filings_fetch(verbose=verbose))
            print(f"✓ EDGAR filings fetch scheduled: daily at {FILINGS_FETCH_TIME} ET")
        except Exception as e:
            print(f"⚠️  Could not schedule filings fetch: {e}")
    if OPTIONS_FETCH_ENABLED:
        try:
            schedule.every().day.at(OPTIONS_FETCH_TIME).do(lambda: scheduled_options_fetch(verbose=verbose))
            print(f"✓ Options IV snapshot scheduled: daily at {OPTIONS_FETCH_TIME} ET")
        except Exception as e:
            print(f"⚠️  Could not schedule options fetch: {e}")

    # Graph maintenance (off-market): daily close-stale + embedding purge, weekly dedup merge.
    # These bound the news->graph fan-out that otherwise grows unbounded.
    if GRAPH_MAINTENANCE_ENABLED:
        try:
            schedule.every().day.at(GRAPH_CLEANUP_TIME).do(lambda: scheduled_graph_cleanup(verbose=verbose))
            merge_job = getattr(schedule.every(), GRAPH_MERGE_WEEKDAY)
            merge_job.at(GRAPH_MERGE_TIME).do(lambda: scheduled_graph_merge(verbose=verbose))
            print(f"✓ Graph maintenance scheduled: daily {GRAPH_CLEANUP_TIME} (close+purge), "
                  f"{GRAPH_MERGE_WEEKDAY} {GRAPH_MERGE_TIME} (merge)")
        except Exception as e:
            print(f"⚠️  Could not schedule graph maintenance: {e}")

    et_now = datetime.now(ET_TIMEZONE)

    _init_graph_tables()
    run_startup_db_maintenance(verbose=verbose)
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

