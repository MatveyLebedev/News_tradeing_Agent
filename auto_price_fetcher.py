"""Nightly minute-bar sweep (the `price-fetcher` container).

Runs after the close, re-requests the last few sessions, and lets the writer's
ON CONFLICT DO NOTHING fill anything the 5-minute intraday fetch in the news container
missed. That repair role is the reason this job exists alongside the other one.

History (2026-07-29): this module used to import `price_parser.py`, a second, unmaintained
minute writer whose INSERT targeted a column named `date` — the tables have `timestamp`.
Every row therefore raised, the first failure aborted the transaction, and the job printed
"✓ Inserted 0 records" on its way to writing nothing, every night. It now uses the same
`price_fetcher` module the news container writes with, so there is ONE writer to keep
correct instead of two that can disagree.
"""
import os
import time
from datetime import datetime

import pytz
import schedule

from price_fetcher import DEFAULT_TICKERS, fetch_all_tickers, report_gaps
from graph_db import update_price_movements

# US Eastern Time zone
ET_TIMEZONE = pytz.timezone('America/New_York')

# Configuration - read from environment variables or use defaults
FETCH_TIME = os.getenv("FETCH_TIME", "22:00")  # 10:00 PM ET (6 hours after market close at 4:00 PM)
TICKERS = os.getenv("TICKERS").split(",") if os.getenv("TICKERS") else DEFAULT_TICKERS
INTERVAL = os.getenv("FETCH_INTERVAL", "1m")
# 5d, not 1d: this is the repair sweep, and a week-wide re-request is still ONE call per
# ticker (yfinance serves '5d' directly) while covering a hole opened earlier in the week.
PERIOD = os.getenv("FETCH_PERIOD", "5d")


def fetch_and_save_daily_data():
    """
    Fetch 1-minute data for all tickers and save to database
    This runs daily at 10 PM ET (6 hours after market close)
    """
    et_now = datetime.now(ET_TIMEZONE)
    
    print("\n" + "=" * 80)
    print(f"AUTOMATED DAILY DATA FETCH - {et_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 80)
    print(f"Fetching 1-minute data for {len(TICKERS)} tickers...")
    print(f"Period: {PERIOD} | Interval: {INTERVAL}")
    print()
    
    try:
        # fetch_all_tickers fetches AND writes; the writer creates any missing table and
        # skips bars already stored, so re-running is idempotent by design.
        results = fetch_all_tickers(tickers=TICKERS, period=PERIOD, verbose=True)

        print("\n" + "=" * 80)
        print("DAILY FETCH COMPLETE")
        print("=" * 80)
        print(f"✓ Successfully fetched: {results['success']}/{len(TICKERS)} tickers")
        print(f"✓ Rows inserted: {results['total_rows']}")
        if results['failed']:
            print(f"✗ Failed: {results['failed']} tickers")
        print(f"Next fetch scheduled for: {FETCH_TIME} ET tomorrow")
        print("=" * 80)

        # Surface holes while they can still be repaired. Yahoo serves 1-minute bars for
        # ~30 days only, so a gap noticed later is permanent — the 2026-06-23 outage
        # (23 bars for every ticker) was found months afterwards, by an experiment.
        print("\nChecking minute-bar coverage...")
        try:
            report_gaps(tickers=TICKERS)
        except Exception as e:
            print(f"⚠ Coverage check failed: {e}")

        print("\nComputing graph price movements...")
        try:
            update_price_movements()
            print("✓ Graph price movements updated")
        except Exception as e:
            print(f"⚠ Graph price movement update failed: {e}")

    except Exception as e:
        print(f"\n❌ ERROR during fetch: {e}")
        print("Will retry tomorrow at scheduled time.")


def check_market_day():
    """
    Check if today is a trading day (Monday-Friday, excluding major holidays)
    Returns True if it's a trading day
    """
    et_now = datetime.now(ET_TIMEZONE)
    weekday = et_now.weekday()
    
    # Monday = 0, Sunday = 6
    # Trading days are Monday-Friday (0-4)
    if weekday >= 5:  # Saturday or Sunday
        print(f"📅 Skipping: Today is {et_now.strftime('%A')} (Weekend)")
        return False
    
    # TODO: Add holiday checking if needed
    # For now, we'll fetch on all weekdays
    return True


def scheduled_fetch_with_check():
    """Wrapper that checks if it's a trading day before fetching"""
    if check_market_day():
        fetch_and_save_daily_data()
    else:
        et_now = datetime.now(ET_TIMEZONE)
        print(f"\n⏭️  Skipping data fetch - {et_now.strftime('%Y-%m-%d')} is not a trading day")


def run_scheduler():
    """
    Run the scheduler that fetches data daily at 10 PM ET
    """
    # Schedule the job
    schedule.every().day.at(FETCH_TIME).do(scheduled_fetch_with_check)
    
    et_now = datetime.now(ET_TIMEZONE)
    
    print("=" * 80)
    print("AUTOMATED PRICE FETCHER - STARTED")
    print("=" * 80)
    print(f"Current time: {et_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Schedule: Daily at {FETCH_TIME} ET (6 hours after market close)")
    print(f"Tickers: {', '.join(TICKERS)}")
    print(f"Interval: {INTERVAL} | Period: {PERIOD}")
    print()
    print("📊 The scheduler will:")
    print("  1. Wait until 10:00 PM ET every day")
    print("  2. Check if it's a trading day (Mon-Fri)")
    print("  3. Fetch 1-minute data for the current trading day")
    print("  4. Save all data to the database")
    print("  5. Repeat daily")
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
            time.sleep(60)  # Check every minute
    except KeyboardInterrupt:
        print("\n\n" + "=" * 80)
        print("SCHEDULER STOPPED")
        print("=" * 80)
        print("Automated fetching has been terminated.")


def run_now_and_schedule():
    """
    Run the fetch immediately, then start the scheduler
    Useful for testing
    """
    print("🚀 Running immediate fetch before starting scheduler...\n")
    scheduled_fetch_with_check()
    print("\n" + "=" * 80)
    print("Starting scheduler for future runs...")
    print("=" * 80)
    time.sleep(2)
    run_scheduler()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Automated daily fetcher for 1-minute stock price data'
    )
    parser.add_argument(
        '--now', 
        action='store_true',
        help='Fetch data immediately before starting scheduler'
    )
    parser.add_argument(
        '--once', 
        action='store_true',
        help='Fetch data once and exit (no scheduling)'
    )
    parser.add_argument(
        '--tickers',
        nargs='+',
        default=DEFAULT_TICKERS,
        help='List of ticker symbols to fetch'
    )
    
    args = parser.parse_args()
    
    # Update tickers if specified
    if args.tickers != DEFAULT_TICKERS:
        TICKERS = args.tickers
        print(f"Using custom tickers: {', '.join(TICKERS)}")
    
    if args.once:
        # Run once and exit
        scheduled_fetch_with_check()
    elif args.now:
        # Run now and then start scheduler
        run_now_and_schedule()
    else:
        # Just start the scheduler
        run_scheduler()

