import yfinance as yf
import psycopg2
from psycopg2 import Error
from datetime import datetime, timedelta
import time
import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist

# Database configuration - read from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "news"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432")
}

# Popular tickers to track (further expanded list)
POPULAR_TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'NVDA', 'META',
    'TSLA', 'AMD', 'NFLX', 'PLTR', 'SPY', 'QQQ',
    'INTC', 'BABA', 'CRM', 'ORCL', 'IBM', 'CSCO', 'UBER', 'LYFT',
    'DIS', 'SHOP', 'SQ', 'PYPL', 'ABNB', 'COIN', 'SNAP', 'TWLO',
    'ZM', 'DOCU', 'ADBE', 'MRNA', 'BA', 'GE', 'JPM', 'GS', 'BAC',
    'V', 'MA', 'WMT', 'TGT', 'COST', 'HD', 'LOW', 'PEP', 'KO', 'SBUX',
    'NKE', 'MCD', 'WFC', 'CVX', 'XOM', 'LULU', 'BBY', 'FDX', 'UPS', 'F',
    'GM', 'BYND', 'ROKU', 'ARCC', 'PFE', 'JNJ', 'UNH', 'LLY', 'TMO', 'AMGN',
    'AAL', 'DAL', 'LUV', 'UAL', 'CAT', 'MMM', 'DOW', 'HON', 'MMM', 'MO',
    'T', 'VZ', 'TMUS', 'S', 'CL', 'PG', 'KHC', 'KR', 'DG', 'DLTR', 'SIRI',
    'SPOT', 'MTCH', 'QCOM', 'TXN', 'AVGO', 'ASML', 'NXPI', 'ADI', 'SWKS',
    'ENPH', 'FSLR', 'SEDG', 'TSMC', 'BIDU', 'JD', 'PDD', 'NTES', 'SE',
    'RBLX', 'ATVI', 'EA', 'TTWO', 'NVAX', 'BNTX', 'VRTX', 'REGN', 'BIIB',
    'GILD', 'SNY', 'AZN', 'MRK', 'C', 'SCHW', 'MS', 'BK', 'BLK'
]


def get_db_connection():
    """Establish connection to PostgreSQL database"""
    try:
        connection = psycopg2.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f"Error connecting to database: {e}")
        return None


def validate_period_interval(period, interval):
    """
    Validate if period and interval combination is supported
    
    Returns:
        (is_valid, warning_message)
    """
    # Define limitations based on Yahoo Finance API
    interval_limits = {
        '1m': ['1d', '5d', '7d'],  # Max 8 days
        '2m': ['1d', '5d', '1mo'],  # Max ~60 days
        '5m': ['1d', '5d', '1mo'],  # Max ~60 days
        '15m': ['1d', '5d', '1mo'],  # Max ~60 days
        '30m': ['1d', '5d', '1mo'],  # Max ~60 days
        '60m': ['1d', '5d', '1mo', '3mo', '6mo', '1y'],  # Max ~730 days
        '90m': ['1d', '5d', '1mo', '3mo', '6mo', '1y'],  # Max ~730 days
        '1h': ['1d', '5d', '1mo', '3mo', '6mo', '1y'],   # Max ~730 days
        '1d': ['1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'],  # No limit
    }
    
    if interval not in interval_limits:
        return True, None  # Unknown interval, let API handle it
    
    allowed_periods = interval_limits[interval]
    
    if period not in allowed_periods:
        warning = f"⚠️  WARNING: {interval} interval with {period} period may fail. "
        if interval == '1m':
            warning += "1-minute data limited to 7 days maximum."
        elif interval in ['2m', '5m', '15m', '30m']:
            warning += f"This interval is limited to ~60 days. Recommended: use '1mo' or less."
        elif interval in ['60m', '90m', '1h']:
            warning += f"This interval is limited to ~730 days. Recommended: use '1y' or less."
        return False, warning
    
    return True, None


def parse_ticker_data(ticker, period='1d', interval='1m'):
    """
    Parse price data for a single ticker
    
    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL')
        period: Data period - 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
        interval: Data interval - 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo
    
    IMPORTANT LIMITATIONS (Yahoo Finance):
        - 1m interval: Maximum 7 days per request (8 days limit)
        - 2m, 5m, 15m, 30m intervals: Maximum 60 days per request
        - 60m, 90m intervals: Maximum 730 days per request
        - 1d interval: No limit
    
    Returns:
        DataFrame with price data or None if error
    """
    try:
        # Validate period/interval combination
        is_valid, warning = validate_period_interval(period, interval)
        if warning:
            print(warning)
        
        print(f"Fetching data for {ticker}...")
        stock = yf.Ticker(ticker)
        
        # Get historical data
        df = stock.history(period=period, interval=interval)
        
        if df.empty:
            print(f"  No data available for {ticker}")
            return None
        
        print(f"  ✓ Fetched {len(df)} data points for {ticker}")
        return df
        
    except Exception as e:
        print(f"  ✗ Error fetching {ticker}: {e}")
        return None


def get_current_price(ticker):
    """Get current price and info for a ticker"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        data = {
            'ticker': ticker,
            'current_price': info.get('currentPrice', info.get('regularMarketPrice')),
            'open': info.get('regularMarketOpen'),
            'high': info.get('dayHigh'),
            'low': info.get('dayLow'),
            'volume': info.get('volume'),
            'market_cap': info.get('marketCap'),
            'pe_ratio': info.get('trailingPE'),
            'change_percent': info.get('regularMarketChangePercent'),
        }
        return data
    except Exception as e:
        print(f"Error getting current price for {ticker}: {e}")
        return None


def parse_all_tickers(tickers=POPULAR_TICKERS, period='1d', interval='1m'):
    """
    Parse price data for multiple tickers
    
    Args:
        tickers: List of ticker symbols
        period: Data period
        interval: Data interval
    
    Returns:
        Dictionary with ticker as key and DataFrame as value
    """
    print("=" * 80)
    print(f"PARSING PRICE DATA FOR {len(tickers)} TICKERS")
    print("=" * 80)
    print(f"Period: {period} | Interval: {interval}\n")
    
    results = {}
    
    for ticker in tickers:
        df = parse_ticker_data(ticker, period, interval)
        if df is not None:
            results[ticker] = df
        time.sleep(0.5)  # Be polite to API
    
    print(f"\n{'='*80}")
    print(f"Successfully fetched data for {len(results)}/{len(tickers)} tickers")
    print("=" * 80)
    
    return results


def display_current_prices(tickers=POPULAR_TICKERS):
    """Display current prices for all tickers"""
    print("\n" + "=" * 80)
    print("CURRENT MARKET PRICES")
    print("=" * 80)
    print(f"\n{'Ticker':<8} {'Price':>10} {'Open':>10} {'High':>10} {'Low':>10} {'Volume':>15} {'Change %':>10}")
    print("-" * 80)
    
    for ticker in tickers:
        data = get_current_price(ticker)
        if data:
            price = data['current_price'] or 0
            open_p = data['open'] or 0
            high = data['high'] or 0
            low = data['low'] or 0
            volume = data['volume'] or 0
            change = data['change_percent'] or 0
            
            print(f"{ticker:<8} ${price:>9.2f} ${open_p:>9.2f} ${high:>9.2f} ${low:>9.2f} {volume:>15,} {change:>9.2f}%")
        time.sleep(0.3)
    
    print("=" * 80)


def save_to_database(ticker, df):
    """
    Save parsed price data to database
    
    Args:
        ticker: Stock ticker symbol
        df: DataFrame with price data (must have columns: Open, High, Low, Close, Volume)
    """
    connection = get_db_connection()
    if not connection:
        return False
    
    try:
        cursor = connection.cursor()
        table_name = f"historical_data_{ticker.lower()}"
        
        # Check if table exists
        cursor.execute(f"""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = '{table_name}'
            );
        """)
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print(f"Table {table_name} does not exist. Skipping...")
            return False
        
        # Insert data
        inserted = 0
        for index, row in df.iterrows():
            try:
                cursor.execute(f"""
                    INSERT INTO {table_name} (date, open, high, low, close, volume, fetch_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date) DO NOTHING;
                """, (
                    index,
                    float(row['Open']),
                    float(row['High']),
                    float(row['Low']),
                    float(row['Close']),
                    int(row['Volume']),
                    datetime.now()
                ))
                inserted += 1
            except Exception as e:
                print(f"Error inserting row for {ticker}: {e}")
                continue
        
        connection.commit()
        print(f"✓ Inserted {inserted} records into {table_name}")
        
        cursor.close()
        return True
        
    except Error as e:
        print(f"Database error: {e}")
        return False
    finally:
        if connection:
            connection.close()


def display_ticker_summary(ticker, df):
    """Display summary statistics for a ticker"""
    print(f"\n{'='*80}")
    print(f"SUMMARY FOR {ticker}")
    print(f"{'='*80}")
    print(f"Data points: {len(df)}")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    print(f"\nPrice Statistics:")
    print(f"  Open:   ${df['Open'].iloc[0]:.2f}")
    print(f"  Close:  ${df['Close'].iloc[-1]:.2f}")
    print(f"  High:   ${df['High'].max():.2f}")
    print(f"  Low:    ${df['Low'].min():.2f}")
    print(f"  Avg:    ${df['Close'].mean():.2f}")
    print(f"\nVolume:")
    print(f"  Total:  {df['Volume'].sum():,}")
    print(f"  Avg:    {df['Volume'].mean():,.0f}")
    print(f"  Max:    {df['Volume'].max():,}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Parse stock price data for popular tickers')
    parser.add_argument('--tickers', nargs='+', default=POPULAR_TICKERS,
                       help='List of ticker symbols (default: popular tech stocks)')
    parser.add_argument('--period', default='1d', 
                       choices=['1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'],
                       help='Data period (default: 1d)')
    parser.add_argument('--interval', default='1m',
                       choices=['1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo', '3mo'],
                       help='Data interval (default: 1m)')
    parser.add_argument('--current', action='store_true',
                       help='Display current prices only')
    parser.add_argument('--save-db', action='store_true',
                       help='Save data to database')
    parser.add_argument('--summary', action='store_true',
                       help='Show detailed summary for each ticker')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("STOCK PRICE PARSER")
    print("=" * 80)
    
    # Display current prices if requested
    if args.current:
        display_current_prices(args.tickers)
    else:
        # Parse historical data
        results = parse_all_tickers(args.tickers, args.period, args.interval)
        
        # Show summaries if requested
        if args.summary:
            for ticker, df in results.items():
                display_ticker_summary(ticker, df)
        
        # Save to database if requested
        if args.save_db:
            print("\n" + "=" * 80)
            print("SAVING TO DATABASE")
            print("=" * 80)
            for ticker, df in results.items():
                save_to_database(ticker, df)
    
    print("\nDone!")

