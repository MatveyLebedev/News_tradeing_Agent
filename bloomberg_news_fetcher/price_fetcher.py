"""
Stock price fetcher V2 - Per-ticker tables
Fetches 1-minute historical data and stores in separate tables per ticker
"""
import yfinance as yf
import psycopg2
from datetime import datetime, timedelta
import pytz
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
    'PYPL', 'ADP', 'UBER', 'SQ', 'ABNB',
    
    # Utilities & Energy
    'NEE', 'DUK', 'SO', 'AEP',
    
    # Materials & Chemicals
    'LIN', 'APD', 'SHW', 'ECL', 'NEM',
    
    # Real Estate & REITs
    'PLD', 'AMT', 'CCI', 'EQIX', 'PSA'
]


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


def fetch_1m_prices(ticker, period='1d'):
    """
    Fetch 1-minute price data for a ticker
    
    Args:
        ticker: Stock ticker symbol
        period: Time period ('1d', '5d', '1mo')
        
    Returns:
        DataFrame with price data or None
    """
    try:
        stock = yf.Ticker(ticker)
        # Download 1-minute data
        df = stock.history(period=period, interval='1m')
        
        if df.empty:
            return None
        
        return df
        
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        logger.error('PRICE_FETCH_ERROR', f'Failed to fetch prices for {ticker}',
                    details={'ticker': ticker, 'error': str(e), 'period': period})
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
        rows = []
        for timestamp, row in df.iterrows():
            try:
                rows.append((
                    timestamp.replace(tzinfo=None),
                    float(row['Open']),
                    float(row['High']),
                    float(row['Low']),
                    float(row['Close']),
                    int(row['Volume']),
                    fetch_time_naive,
                ))
            except Exception as e:
                print(f"  Skipping malformed row for {ticker} at {timestamp}: {e}")

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


def fetch_all_tickers(tickers=None, period='1d', verbose=False):
    """
    Fetch 1-minute price data for multiple tickers
    
    Args:
        tickers: List of ticker symbols (default: TOP_50_TICKERS)
        period: Time period to fetch
        verbose: If True, prints progress logs (default: False)
        
    Returns:
        Dictionary with results
    """
    if tickers is None:
        tickers = TOP_50_TICKERS
    
    if verbose:
        print(f"\n{'=' * 80}")
        print(f"FETCHING 1-MINUTE PRICE DATA (PER-TICKER TABLES)")
        print(f"{'=' * 80}")
        print(f"Tickers: {len(tickers)}")
        print(f"Period: {period}")
        print(f"Interval: 1 minute")
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
        
        df = fetch_1m_prices(ticker, period=period)
        
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
                print(f"✗ Failed")
            results['failed'] += 1
    
    if verbose:
        print(f"\n{'=' * 80}")
        print(f"FETCH COMPLETE")
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
        choices=['1d', '5d', '1mo'],
        help='Time period to fetch (default: 1d)'
    )
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
    else:
        tickers = args.tickers if args.tickers else TOP_50_TICKERS
        results = fetch_all_tickers(tickers=tickers, period=args.period)
        print(f"\nProcessed {len(tickers)} tickers")
        print(f"Total rows inserted: {results['total_rows']}")
        print(f"Tables created/used: {len(results['tables_created'])}")

