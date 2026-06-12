"""
Tool for time-aware database queries
All timestamps use US Eastern Time (America/New_York) for consistency

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import pandas as pd
import pytz
from langchain_core.tools import tool
import json

# US Eastern Time timezone (handles EST/EDT automatically)
EASTERN_TZ = pytz.timezone('America/New_York')


def _get_current_time_est(timestamp: datetime) -> datetime:
    """
    Get current time in US Eastern Time.
    Ensures timestamp and current time are in the same timezone for comparison.
    
    Args:
        timestamp: The timestamp to compare against (used to determine timezone handling)
        
    Returns:
        Current time in Eastern Time (timezone-aware)
    """
    # Always get current time in Eastern Time
    return datetime.now(EASTERN_TZ)


def _ensure_est(dt: datetime) -> datetime:
    """
    Convert datetime to US Eastern Time (timezone-aware).
    All datetimes must be in EST for consistency.
    
    Args:
        dt: datetime (naive or aware)
        
    Returns:
        timezone-aware datetime in US Eastern Time
    """
    if dt.tzinfo is None:
        # Naive datetime - assume it's already in Eastern Time
        return EASTERN_TZ.localize(dt)
    else:
        # Already timezone-aware - convert to Eastern Time
        return dt.astimezone(EASTERN_TZ)


def _resample_to_15min(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample price data to 15-minute intervals.
    Keeps only records at :00, :15, :30, :45 minutes.
    
    Args:
        df: DataFrame with 'date' column and OHLCV data
        
    Returns:
        Resampled DataFrame
    """
    if df.empty:
        return df
    
    # Set date as index for resampling
    df_indexed = df.set_index('date')
    
    # Resample to 15-minute intervals, taking the last value in each interval
    # This aggregates data to 15-minute buckets
    resampled = df_indexed.resample('15min').agg({
        'open': 'first',   # First open in the interval
        'high': 'max',     # Maximum high in the interval
        'low': 'min',      # Minimum low in the interval
        'close': 'last',   # Last close in the interval
        'volume': 'sum'    # Sum of volume in the interval
    })
    
    # Reset index to get date back as column
    resampled = resampled.reset_index()
    resampled = resampled.rename(columns={'date': 'date'})
    
    return resampled


def get_historical_prices(
    ticker: str,
    timestamp: datetime,
    lookback_days: int,
    db_manager: Any,
    resample_15min: bool = False
) -> Optional[pd.DataFrame]:
    """
    Get historical price data before timestamp (time-aware to prevent data leakage)
    All timestamps use US Eastern Time (EST/EDT)
    
    Note: Not decorated with @tool - called directly by wrapper in nodes.py
    
    Args:
        ticker: Stock ticker symbol
        timestamp: Reference timestamp (only data before this is returned)
        lookback_days: Number of days to look back (default: 1)
        db_manager: DBManager instance
        resample_15min: If True, resample data to 15-minute intervals
        
    Returns:
        DataFrame with columns: date, open, high, low, close, volume
        Returns None if no data available or ticker table doesn't exist
    """
    
    # Convert timestamp to EST
    timestamp_est = _ensure_est(timestamp)
    
    # Validate timestamp is not in the future (both in EST)
    current_time_est = _get_current_time_est(timestamp_est)
    if timestamp_est > current_time_est:
        raise ValueError(
            f"Timestamp {timestamp_est.strftime('%Y-%m-%d %H:%M:%S %Z')} is in the future. "
            f"This would cause data leakage."
        )
    
    # Check if table exists
    if not db_manager.table_exists(ticker):
        print(f"No historical data table found for {ticker}")
        return None
    
    # Get historical prices (pass EST timestamp)
    df = db_manager.get_historical_prices(ticker, timestamp_est, lookback_days)
    
    if df is not None and not df.empty:
        # Apply 15-minute resampling if requested
        if resample_15min:
            df = _resample_to_15min(df)
            print(f"✓ Retrieved {len(df)} price records for {ticker} (15-min intervals)")
        else:
            print(f"✓ Retrieved {len(df)} price records for {ticker}")
        return df
    else:
        print(f"No historical data found for {ticker}")
        return None


def get_price_at_time(
    ticker: str,
    timestamp: datetime,
    db_manager: Any
) -> Optional[Dict[str, Any]]:
    """
    Get the closest price data before timestamp
    All timestamps use US Eastern Time (EST/EDT)
    
    Note: Not decorated with @tool - called directly by wrapper in nodes.py
    
    Args:
        ticker: Stock ticker symbol
        timestamp: Reference timestamp
        db_manager: DBManager instance
        
    Returns:
        Dict with keys: date, open, high, low, close, volume
        Returns None if no data available
    """
    
    # Convert timestamp to EST
    timestamp_est = _ensure_est(timestamp)
    
    # Validate timestamp is not in the future (both in EST)
    current_time_est = _get_current_time_est(timestamp_est)
    if timestamp_est > current_time_est:
        raise ValueError(
            f"Timestamp {timestamp_est.strftime('%Y-%m-%d %H:%M:%S %Z')} is in the future. "
            f"This would cause data leakage."
        )
    
    # Check if table exists
    if not db_manager.table_exists(ticker):
        print(f"No historical data table found for {ticker}")
        return None
    
    # Get price at time (pass EST timestamp)
    price_data = db_manager.get_price_at_time(ticker, timestamp_est)
    
    if price_data:
        print(f"✓ Retrieved price for {ticker} at {price_data['date']}")
        return price_data
    else:
        print(f"No price data found for {ticker} before {timestamp}")
        return None


# ============================================================================
# LLM-Callable Tools (with @tool decorator)
# ============================================================================

def create_get_price_data_tool(timestamp: datetime, db_manager: Any):
    """Create get_price_data tool with captured context"""
    
    @tool
    def get_price_data_tool(ticker: str) -> str:
        """
        Get historical price data (OHLCV) for a ticker. Use this to analyze price trends.
        Returns data for last 3 days with 15-minute discretization.
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
        
        Returns:
            JSON string with historical price data (date, open, high, low, close, volume)
            Data is resampled to 15-minute intervals for the last 3 days
        """
        # Check if table exists first
        table_exists = db_manager.table_exists(ticker)
        if not table_exists:
            raise ValueError(f"No historical data table found for {ticker}. The table 'historical_data_{ticker.lower()}' does not exist.")
        
        # Fixed lookback: always 3 days
        lookback_days = 3
        
        price_data = get_historical_prices(
            ticker=ticker,
            timestamp=timestamp,
            lookback_days=lookback_days,
            db_manager=db_manager,
            resample_15min=True  # Always use 15-minute discretization
        )
        
        if price_data is not None and not price_data.empty:
            result = {
                "ticker": ticker,
                "records": len(price_data),
                "lookback_days": lookback_days,
                "interval": "15 minutes",
                "data": price_data.to_dict('records')
            }
            return json.dumps(result, default=str)
        else:
            # Table exists but no data in the requested range
            timestamp_est = _ensure_est(timestamp)
            start_date = timestamp_est - timedelta(days=lookback_days)
            raise ValueError(
                f"No price data available for {ticker} in the date range "
                f"{start_date.strftime('%Y-%m-%d %H:%M:%S %Z')} to "
                f"{timestamp_est.strftime('%Y-%m-%d %H:%M:%S %Z')}. "
                f"The table exists but contains no data in this range."
            )
    
    return get_price_data_tool


def create_get_price_at_time_tool(timestamp: datetime, db_manager: Any):
    """Create get_price_at_time tool with captured context"""
    
    @tool
    def get_price_at_time_tool(ticker: str) -> str:
        """
        Get the current price at the news timestamp for a ticker.
    
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
        
        Returns:
            JSON string with price data (open, high, low, close, volume) at timestamp
        """
        price_data = get_price_at_time(
            ticker=ticker,
            timestamp=timestamp,
            db_manager=db_manager
        )

        if price_data:
            return json.dumps(price_data, default=str)
        else:
            raise ValueError(f"No price data available for {ticker} at timestamp. Historical data table may not exist or contains no data.")
    
    return get_price_at_time_tool

