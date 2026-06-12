"""
Stock price fetcher for top 50 US market tickers
Fetches 1-minute historical data using yfinance
"""
import yfinance as yf
import psycopg2
from datetime import datetime, timedelta
import pytz
from config import DB_CONFIG
from logger import DatabaseLogger

# Initialize logger
logger = DatabaseLogger()

# Moscow timezone
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

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
    Save price data to database
    
    Args:
        ticker: Stock ticker symbol
        df: DataFrame with price data
        
    Returns:
        Number of rows inserted
    """
    if df is None or df.empty:
        return 0
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        fetch_time = datetime.now(MOSCOW_TZ)
        inserted_count = 0
        
        for timestamp, row in df.iterrows():
            try:
                # Convert timestamp to timezone-naive for PostgreSQL
                ts = timestamp.replace(tzinfo=None)
                
                insert_query = """
                    INSERT INTO stock_prices (ticker, timestamp, open, high, low, close, volume, fetch_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, timestamp) DO NOTHING;
                """
                
                cursor.execute(insert_query, (
                    ticker,
                    ts,
                    float(row['Open']),
                    float(row['High']),
                    float(row['Low']),
                    float(row['Close']),
                    int(row['Volume']),
                    fetch_time
                ))
                
                if cursor.rowcount > 0:
                    inserted_count += 1
                    
            except Exception as e:
                print(f"  Error inserting row for {ticker} at {timestamp}: {e}")
                continue
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return inserted_count
        
    except Exception as e:
        print(f"Error saving prices for {ticker}: {e}")
        logger.error('PRICE_DB_ERROR', f'Failed to save prices for {ticker}',
                    details={'ticker': ticker, 'error': str(e)})
        return 0


def fetch_all_tickers(tickers=None, period='1d'):
    """
    Fetch 1-minute price data for multiple tickers
    
    Args:
        tickers: List of ticker symbols (default: TOP_50_TICKERS)
        period: Time period to fetch
        
    Returns:
        Dictionary with results
    """
    if tickers is None:
        tickers = TOP_50_TICKERS
    
    print(f"\n{'=' * 80}")
    print(f"FETCHING 1-MINUTE PRICE DATA")
    print(f"{'=' * 80}")
    print(f"Tickers: {len(tickers)}")
    print(f"Period: {period}")
    print(f"Interval: 1 minute")
    print()
    
    logger.info('PRICE_FETCH_START', f'Starting price fetch for {len(tickers)} tickers',
               details={'ticker_count': len(tickers), 'period': period})
    
    results = {
        'success': 0,
        'failed': 0,
        'total_rows': 0,
        'tickers_processed': []
    }
    
    for idx, ticker in enumerate(tickers, 1):
        print(f"[{idx}/{len(tickers)}] {ticker}... ", end='', flush=True)
        
        df = fetch_1m_prices(ticker, period=period)
        
        if df is not None and not df.empty:
            rows_inserted = save_prices_to_db(ticker, df)
            
            if rows_inserted > 0:
                print(f"✓ {rows_inserted} rows inserted")
                results['success'] += 1
                results['total_rows'] += rows_inserted
                results['tickers_processed'].append(ticker)
                
                logger.info('PRICE_FETCH_SUCCESS', f'Fetched {rows_inserted} rows for {ticker}',
                           details={'ticker': ticker, 'rows': rows_inserted})
            else:
                print(f"⚠ No new data (already exists)")
                results['success'] += 1
        else:
            print(f"✗ Failed")
            results['failed'] += 1
    
    print(f"\n{'=' * 80}")
    print(f"FETCH COMPLETE")
    print(f"{'=' * 80}")
    print(f"Success: {results['success']}/{len(tickers)}")
    print(f"Failed: {results['failed']}/{len(tickers)}")
    print(f"Total rows inserted: {results['total_rows']}")
    print(f"{'=' * 80}\n")
    
    logger.info('PRICE_FETCH_COMPLETE', 
               f"Price fetch complete: {results['success']} success, {results['failed']} failed",
               details=results)
    
    return results


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Fetch 1-minute historical price data for US stocks'
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
        help='Specific tickers to fetch (default: top 50)'
    )
    
    args = parser.parse_args()
    
    tickers = args.tickers if args.tickers else TOP_50_TICKERS
    
    results = fetch_all_tickers(tickers=tickers, period=args.period)
    
    print(f"\nProcessed {len(tickers)} tickers")
    print(f"Total rows inserted: {results['total_rows']}")

