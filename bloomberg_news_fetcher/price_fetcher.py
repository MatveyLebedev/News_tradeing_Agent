"""
Stock price fetcher V2 - Per-ticker tables
Fetches 1-minute historical data and stores in separate tables per ticker
"""
import math
from datetime import date, datetime, timedelta

import pandas as pd
import psycopg2
import pytz
import yfinance as yf

from config import DB_CONFIG
from logger import DatabaseLogger

# Initialize logger
logger = DatabaseLogger()

# US Eastern Time timezone (for consistency with trading agent and US market hours)
EASTERN_TZ = pytz.timezone('America/New_York')

# Top 100+ US market tickers by market cap (as of 2025)
# Source: S&P 100 and Russell Top 50 Index components
TOP_50_TICKERS = [
    # Mega cap tech (Top 10)
    'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'ORCL',
    
    # Finance (Top financial institutions)
    'BRK-B', 'JPM', 'V', 'MA', 'BAC', 'WFC', 'MS', 'GS', 'BLK', 'C', 'AXP', 'SPGI', 'CB',
    
    # Healthcare & Pharma
    'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'TMO', 'ABT', 'DHR', 'PFE', 'BMY', 'AMGN', 'GILD',
    'CVS', 'MDT', 'ISRG', 'CI', 'REGN', 'VRTX', 'HUM',
    
    # Consumer & Retail
    'WMT', 'HD', 'COST', 'PG', 'KO', 'PEP', 'MCD', 'NKE', 'SBUX', 'TGT', 'LOW', 'TJX',
    'EL', 'BKNG', 'CMG',
    
    # Energy
    'XOM', 'CVX', 'COP', 'SLB', 'EOG', 'PSX', 'MPC',
    
    # Industrials
    'CAT', 'BA', 'HON', 'UNP', 'RTX', 'GE', 'LMT', 'DE', 'UPS', 'MMM', 'NOC',
    
    # Tech & Communications
    'CRM', 'ADBE', 'CSCO', 'ACN', 'AMD', 'INTC', 'IBM', 'TXN', 'QCOM', 'AMAT', 'NOW',
    'INTU', 'MU', 'PANW', 'LRCX', 'SNPS', 'CDNS', 'ADSK', 'FTNT',
    
    # Media & Entertainment
    'DIS', 'NFLX', 'CMCSA', 'T', 'VZ', 'CHTR', 'TMUS',
    
    # Consumer Services & E-commerce
    # SQ renamed to XYZ (Block Inc.) — the old symbol stopped resolving; the daily price
    # fetcher errored on it every run ("possibly delisted"). historical_data_sq keeps the
    # pre-rename bars; XYZ accumulates from its own backfill onward.
    'PYPL', 'ADP', 'UBER', 'XYZ', 'ABNB',
    
    # Utilities & Energy
    'NEE', 'DUK', 'SO', 'AEP',
    
    # Materials & Chemicals
    'LIN', 'APD', 'SHW', 'ECL', 'NEM',
    
    # Real Estate & REITs
    'PLD', 'AMT', 'CCI', 'EQIX', 'PSA'
]

# Intraday benchmarks — the minute-bar mirror of daily_price_fetcher.BENCHMARK_TICKERS.
# Kept OUT of TOP_50_TICKERS on purpose: that list feeds
# LongTermAgentConfig.get_universe(), so it must stay agent-analyzable companies only —
# the long-term agent would otherwise try to build a news background for an ETF.
#
# Why these tables did not exist until 2026-07-29: every minute fetch ever run passed the
# bare company list, so the intraday side has 121 single names and ZERO ETFs, while the
# DAILY side has had SPY + all 11 sector ETFs for a while. That gap is what forces an
# intraday experiment to synthesise its market factor from an equal-weight basket
# (longterm_agent/holding_curve_lt.py) instead of netting against SPY.
#
# ⚠ This fixes the data GOING FORWARD only. Yahoo serves 1-minute bars for the last
# ~30 days and at most 8 days per request (measured 2026-07-29: 2026-07-01 returns bars,
# 2026-06-29 returns empty), so no run of this fetcher can backfill an older window.
# Recovering one needs a vendor with intraday history; FMP is wired here for earnings on
# the free tier only.
BENCHMARK_TICKERS = ["SPY", "XLB", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI",
                     "XLRE", "XLK", "XLU"]
DEFAULT_TICKERS = TOP_50_TICKERS + BENCHMARK_TICKERS


def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            database=DB_CONFIG['database'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            port=DB_CONFIG['port']
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        raise


def _finite(v):
    """float(v) when it is a real number, else None — the NaN gate for every written bar."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def get_table_name(ticker):
    """
    Get table name for a ticker
    Format: historical_data_{ticker_lowercase}
    Handle special characters (e.g., BRK-B -> historical_data_brk_b)
    """
    clean_ticker = ticker.lower().replace('-', '_').replace('.', '_')
    return f"historical_data_{clean_ticker}"


def create_ticker_table(ticker):
    """
    Create table for a specific ticker if it doesn't exist
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        True if table exists/created, False otherwise
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        table_name = get_table_name(ticker)
        
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL UNIQUE,
            open DECIMAL(12, 4),
            high DECIMAL(12, 4),
            low DECIMAL(12, 4),
            close DECIMAL(12, 4),
            volume BIGINT,
            fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- No extra timestamp index: the UNIQUE(timestamp) constraint above already
        -- creates a btree that serves range/ORDER BY queries. A separate
        -- idx_..._timestamp was redundant, and idx_..._fetch_time was never queried
        -- (fetch_time is insert-only) — both dropped to cut index disk + insert cost.
        COMMENT ON TABLE {table_name} IS '1-minute historical price data for {ticker}';
        """
        
        cursor.execute(create_table_query)
        conn.commit()
        cursor.close()
        conn.close()
        
        return True
        
    except Exception as e:
        print(f"Error creating table for {ticker}: {e}")
        logger.error('PRICE_TABLE_ERROR', f'Failed to create table for {ticker}',
                    details={'ticker': ticker, 'error': str(e)})
        return False


# Yahoo's two hard limits on 1-minute bars, both measured against the live API on
# 2026-07-29 rather than taken from documentation:
#   * per request — "Only 8 days worth of 1m granularity data are allowed to be fetched
#     per request"; we chunk at 7 to keep a margin.
#   * total history — "The requested range must be within the last 30 days". Probed:
#     a window starting 2026-07-01 returned bars, 2026-06-29 returned empty.
# The second one is why an older window can never be recovered from this source, and why
# a coverage hole has to be found and repaired INSIDE the 30-day window or not at all
# (see report_gaps).
MAX_1M_SPAN_DAYS = 7
MAX_1M_HISTORY_DAYS = 29

# Periods small enough for a single request keep the original, proven call shape.
_DIRECT_PERIODS = {'1d': 1, '5d': 5, '7d': 7}
_PERIOD_DAYS = {**_DIRECT_PERIODS, '1mo': 30}


def _resolve_window(period, start, end):
    """(start_date, end_date, note) for a range fetch. `end` is exclusive, yfinance-style."""
    today = datetime.now(EASTERN_TZ).date()
    horizon = today - timedelta(days=MAX_1M_HISTORY_DAYS)
    note = None

    e = date.fromisoformat(end) if end else today + timedelta(days=1)
    s = date.fromisoformat(start) if start else today - timedelta(days=_PERIOD_DAYS.get(period, 1))

    if s < horizon:
        # Say so instead of returning an empty frame: the previous code asked Yahoo for
        # `period='1mo'` at a 1m interval, got a rejection, and reported "no data" — the
        # failure looked identical to a delisted ticker.
        note = (f"1m history reaches back only to {horizon} "
                f"(requested {s}); clamped — older bars need a vendor with intraday history")
        s = horizon
    return s, e, note


def _chunks(start, end, span_days=MAX_1M_SPAN_DAYS):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=span_days), end)
        yield cur, nxt
        cur = nxt


def fetch_1m_prices(ticker, period='1d', start=None, end=None, verbose=False):
    """
    Fetch 1-minute price data for a ticker.

    Args:
        ticker: Stock ticker symbol
        period: '1d' | '5d' | '7d' | '1mo' — ignored when start/end are given
        start, end: 'YYYY-MM-DD' range (end exclusive). Enables targeted repair of a
            specific day, which `period` alone cannot express.

    Anything wider than one request is split into <=7-day chunks and concatenated, so
    '1mo' now returns a month instead of nothing.

    Returns:
        DataFrame indexed by NAIVE US/Eastern timestamps, or None.
    """
    try:
        stock = yf.Ticker(ticker)

        if start is None and end is None and period in _DIRECT_PERIODS:
            df = stock.history(period=period, interval='1m')
        else:
            s, e, note = _resolve_window(period, start, end)
            if note and verbose:
                print(f"  ⚠ {ticker}: {note}")
            frames = []
            for c_start, c_end in _chunks(s, e):
                part = stock.history(start=str(c_start), end=str(c_end), interval='1m')
                if part is not None and not part.empty:
                    frames.append(part)
            if not frames:
                return None
            df = pd.concat(frames)
            df = df[~df.index.duplicated(keep='last')].sort_index()

        if df is None or df.empty:
            return None

        # Convert explicitly rather than dropping the offset. yfinance returns the
        # exchange timezone for US names, so `.replace(tzinfo=None)` happened to be
        # right — but on any row that came back in UTC it would have written bars 4-5
        # hours off, silently, into a column the whole repo reads as naive ET.
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert(EASTERN_TZ).tz_localize(None)

        return df

    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        logger.error('PRICE_FETCH_ERROR', f'Failed to fetch prices for {ticker}',
                    details={'ticker': ticker, 'error': str(e), 'period': period,
                             'start': start, 'end': end})
        return None


def save_prices_to_db(ticker, df):
    """
    Save price data to ticker-specific table
    
    Args:
        ticker: Stock ticker symbol
        df: DataFrame with price data
        
    Returns:
        Number of rows inserted
    """
    if df is None or df.empty:
        return 0
    
    try:
        # Ensure table exists
        if not create_ticker_table(ticker):
            return 0
        
        conn = get_db_connection()
        cursor = conn.cursor()

        table_name = get_table_name(ticker)
        fetch_time_naive = datetime.now(EASTERN_TZ).replace(tzinfo=None)

        # Build all rows as a batch
        rows, dropped = [], 0
        for timestamp, row in df.iterrows():
            try:
                close = _finite(row['Close'])
                if close is None:
                    # Mirrors daily_price_fetcher.save_daily. A NaN close is what cost a
                    # live cohort on 2026-07-24 (BACKTEST_LEDGER `live_0726`): the minute
                    # writer was the one layer that never got the guard, so a poisoned bar
                    # could still reach a table every intraday reader trusts.
                    dropped += 1
                    continue
                rows.append((
                    timestamp.replace(tzinfo=None) if getattr(timestamp, "tzinfo", None)
                    else timestamp,
                    _finite(row['Open']), _finite(row['High']), _finite(row['Low']), close,
                    int(vol) if (vol := _finite(row['Volume'])) is not None else None,
                    fetch_time_naive,
                ))
            except Exception as e:
                dropped += 1
                print(f"  Skipping malformed row for {ticker} at {timestamp}: {e}")

        if dropped:
            print(f"  ⚠ {ticker}: dropped {dropped} non-finite/malformed bar(s)")
            logger.error('PRICE_NONFINITE_BAR', f'{ticker}: {dropped} unusable bars dropped',
                         details={'ticker': ticker, 'dropped': dropped})

        if not rows:
            cursor.close()
            conn.close()
            return 0

        insert_query = f"""
            INSERT INTO {table_name} (timestamp, open, high, low, close, volume, fetch_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (timestamp) DO NOTHING;
        """

        try:
            cursor.executemany(insert_query, rows)
            inserted_count = cursor.rowcount if cursor.rowcount >= 0 else len(rows)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  Error bulk-inserting prices for {ticker}: {e}")
            inserted_count = 0
        finally:
            cursor.close()
            conn.close()

        return inserted_count
        
    except Exception as e:
        print(f"Error saving prices for {ticker}: {e}")
        logger.error('PRICE_DB_ERROR', f'Failed to save prices for {ticker}',
                    details={'ticker': ticker, 'error': str(e)})
        return 0


def fetch_all_tickers(tickers=None, period='1d', verbose=False, start=None, end=None):
    """
    Fetch 1-minute price data for multiple tickers
    
    Args:
        tickers: List of ticker symbols (default: DEFAULT_TICKERS — companies + benchmarks)
        period: Time period to fetch
        verbose: If True, prints progress logs (default: False)
        
    Returns:
        Dictionary with results
    """
    if tickers is None:
        tickers = DEFAULT_TICKERS

    if verbose:
        print(f"\n{'=' * 80}")
        print("FETCHING 1-MINUTE PRICE DATA (PER-TICKER TABLES)")
        print(f"{'=' * 80}")
        print(f"Tickers: {len(tickers)}")
        print(f"Period: {period}")
        print("Interval: 1 minute")
        print()
    
    logger.info('PRICE_FETCH_START', f'Starting price fetch for {len(tickers)} tickers',
               details={'ticker_count': len(tickers), 'period': period, 'storage': 'per-ticker tables'})
    
    results = {
        'success': 0,
        'failed': 0,
        'total_rows': 0,
        'tickers_processed': [],
        'tables_created': []
    }
    
    for idx, ticker in enumerate(tickers, 1):
        if verbose:
            print(f"[{idx}/{len(tickers)}] {ticker}... ", end='', flush=True)
        
        # Create table if needed
        table_name = get_table_name(ticker)
        if create_ticker_table(ticker):
            if table_name not in results['tables_created']:
                results['tables_created'].append(table_name)
        
        df = fetch_1m_prices(ticker, period=period, start=start, end=end, verbose=verbose)

        if df is not None and not df.empty:
            rows_inserted = save_prices_to_db(ticker, df)
            
            if rows_inserted > 0:
                if verbose:
                    print(f"✓ {rows_inserted} rows → {table_name}")
                results['success'] += 1
                results['total_rows'] += rows_inserted
                results['tickers_processed'].append(ticker)
                
                logger.info('PRICE_FETCH_SUCCESS', f'Fetched {rows_inserted} rows for {ticker}',
                           details={'ticker': ticker, 'rows': rows_inserted, 'table': table_name})
            else:
                if verbose:
                    print(f"⚠ No new data (already exists in {table_name})")
                results['success'] += 1
        else:
            if verbose:
                print("✗ Failed")
            results['failed'] += 1
    
    if verbose:
        print(f"\n{'=' * 80}")
        print("FETCH COMPLETE")
        print(f"{'=' * 80}")
        print(f"Success: {results['success']}/{len(tickers)}")
        print(f"Failed: {results['failed']}/{len(tickers)}")
        print(f"Total rows inserted: {results['total_rows']}")
        print(f"Tables used: {len(results['tables_created'])}")
        print(f"{'=' * 80}\n")
    
    logger.info('PRICE_FETCH_COMPLETE', 
               f"Price fetch complete: {results['success']} success, {results['failed']} failed",
               details=results)
    
    return results


def list_ticker_tables():
    """List all ticker-specific tables in the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT tablename 
            FROM pg_tables 
            WHERE schemaname = 'public' 
              AND tablename LIKE 'historical_data_%'
            ORDER BY tablename;
        """)
        
        tables = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return tables
        
    except Exception as e:
        print(f"Error listing tables: {e}")
        return []


FULL_SESSION_BARS = 390  # 09:30–15:59 inclusive


def report_gaps(tickers=None, days=MAX_1M_HISTORY_DAYS, min_bars=FULL_SESSION_BARS,
                verbose=True):
    """Sessions with fewer bars than a full day, for tickers still inside the repair window.

    This exists because a hole is only fixable while Yahoo still serves the day. Two
    outages sit in the current data — 2026-03-27 (~147 bars) and 2026-06-23 (23 bars),
    identical across every ticker, so a feed failure rather than a short session — and
    both were found months later by an experiment, when nothing could be done. Run this
    after a fetch and a fresh hole can simply be re-requested with --start/--end.

    Returns {ticker: [(day, bar_count), ...]} for days below `min_bars`.
    """
    tickers = tickers or DEFAULT_TICKERS
    since = datetime.now(EASTERN_TZ).date() - timedelta(days=days)
    existing = set(list_ticker_tables())
    out = {}

    conn = get_db_connection()
    if conn is None:
        return out
    try:
        with conn.cursor() as cursor:
            for ticker in tickers:
                table = get_table_name(ticker)
                if table not in existing:
                    continue
                cursor.execute(
                    f"SELECT timestamp::date AS d, count(*) FROM {table} "  # noqa: S608
                    "WHERE timestamp >= %s GROUP BY 1 HAVING count(*) < %s ORDER BY 1",
                    (since, min_bars))
                rows = [(d, n) for d, n in cursor.fetchall()]
                if rows:
                    out[ticker] = rows
    finally:
        conn.close()

    if verbose:
        horizon = datetime.now(EASTERN_TZ).date() - timedelta(days=MAX_1M_HISTORY_DAYS)
        if not out:
            print(f"No coverage gaps in the last {days} days "
                  f"({len(tickers)} tickers, threshold {min_bars} bars).")
        else:
            print(f"\nCoverage gaps in the last {days} days "
                  f"(session = {min_bars} bars; ✎ = still refetchable, ✗ = past Yahoo's "
                  f"{MAX_1M_HISTORY_DAYS}-day 1m window and permanent):")
            for ticker, rows in sorted(out.items()):
                shown = ", ".join(f"{'✎' if d >= horizon else '✗'}{d} ({n})"
                                  for d, n in rows[:6])
                more = f" +{len(rows) - 6} more" if len(rows) > 6 else ""
                print(f"  {ticker:6s} {shown}{more}")
            fixable = sorted({d for rows in out.values() for d, _ in rows if d >= horizon})
            if fixable:
                d = fixable[0]
                print(f"\n  repair:  python price_fetcher.py --start {d} "
                      f"--end {d + timedelta(days=1)} --tickers <TICKER>")
            else:
                print(f"\n  nothing repairable: every gap predates {horizon}. Recovering "
                      "these needs a vendor with intraday history.")
    return out


def get_table_stats(table_name):
    """Get statistics for a specific table"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT 
                COUNT(*) as row_count,
                MIN(timestamp) as earliest,
                MAX(timestamp) as latest,
                pg_size_pretty(pg_total_relation_size('{table_name}')) as size
            FROM {table_name};
        """)
        
        stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return {
            'row_count': stats[0],
            'earliest': stats[1],
            'latest': stats[2],
            'size': stats[3]
        }
        
    except Exception as e:
        print(f"Error getting stats for {table_name}: {e}")
        return None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Fetch 1-minute historical price data for US stocks (per-ticker tables)'
    )
    parser.add_argument(
        '--period',
        default='1d',
        choices=['1d', '5d', '7d', '1mo'],
        help='Time period to fetch (default: 1d). Anything over 7d is split into '
             '7-day requests automatically.'
    )
    parser.add_argument('--start', help='Range start YYYY-MM-DD (overrides --period). '
                                        'Use to repair a specific session.')
    parser.add_argument('--end', help='Range end YYYY-MM-DD, exclusive (default: tomorrow)')
    parser.add_argument('--gaps', action='store_true',
                        help='Report sessions with fewer than 390 bars that are still '
                             'inside the 30-day repair window, then exit')
    parser.add_argument(
        '--tickers',
        nargs='+',
        help='Specific tickers to fetch (default: top 120)'
    )
    parser.add_argument(
        '--list-tables',
        action='store_true',
        help='List all ticker tables'
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Show statistics for all ticker tables'
    )
    
    args = parser.parse_args()
    
    if args.list_tables:
        tables = list_ticker_tables()
        print(f"\nFound {len(tables)} ticker tables:")
        for table in tables:
            ticker = table.replace('historical_data_', '').upper().replace('_', '-')
            print(f"  {table} ({ticker})")
        print()
    elif args.stats:
        tables = list_ticker_tables()
        print(f"\nStatistics for {len(tables)} ticker tables:")
        print(f"{'=' * 80}")
        total_rows = 0
        for table in tables:
            stats = get_table_stats(table)
            if stats:
                ticker = table.replace('historical_data_', '').upper().replace('_', '-')
                print(f"{ticker:6s} | {stats['row_count']:6d} rows | {stats['earliest']} to {stats['latest']} | {stats['size']}")
                total_rows += stats['row_count']
        print(f"{'=' * 80}")
        print(f"Total: {total_rows} rows across {len(tables)} tables\n")
    elif args.gaps:
        report_gaps(tickers=args.tickers or DEFAULT_TICKERS)
    else:
        tickers = args.tickers if args.tickers else DEFAULT_TICKERS
        results = fetch_all_tickers(tickers=tickers, period=args.period,
                                    start=args.start, end=args.end, verbose=True)
        print(f"\nProcessed {len(tickers)} tickers")
        print(f"Total rows inserted: {results['total_rows']}")
        print(f"Tables created/used: {len(results['tables_created'])}")

