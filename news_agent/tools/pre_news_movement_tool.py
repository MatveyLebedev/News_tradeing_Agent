"""
Tool for detecting suspicious price movements before news publication
Helps filter out signals where price already leaked
"""
from typing import Dict, Any
from datetime import datetime, timedelta
import pandas as pd


def check_pre_news_movement(
    ticker: str,
    news_timestamp: datetime,
    db_manager: Any,
    window_minutes: int = 60
) -> Dict[str, Any]:
    """
    Check for suspicious price movement before news publication
    
    Detects if price moved significantly before news was published,
    which could indicate information leakage or insider trading
    
    Args:
        ticker: Stock ticker symbol
        news_timestamp: When the news was published
        window_minutes: Time window before news to check (default: 60 min)
        db_manager: Database manager instance
        
    Returns:
        Dict with:
            - suspicious: bool - Whether movement is suspicious
            - price_change_pct: float - Price change % in window
            - volume_change_pct: float - Volume change % vs average
            - flags: List[str] - Specific flags raised
            - pre_news_start: datetime - Start of pre-news window
            - pre_news_end: datetime - End of pre-news window (news time)
    """
    
    flags = []
    suspicious = False
    
    # Define pre-news window
    pre_news_end = news_timestamp
    pre_news_start = news_timestamp - timedelta(minutes=window_minutes)
    
    # Also get baseline period for comparison (previous hour before the window)
    baseline_end = pre_news_start
    baseline_start = baseline_end - timedelta(minutes=window_minutes)
    
    try:
        # Check if table exists
        if not db_manager.table_exists(ticker):
            return {
                "suspicious": False,
                "has_data": False,
                "price_change_pct": 0.0,
                "volume_change_pct": 0.0,
                "flags": ["No historical data available"],
                "pre_news_start": pre_news_start,
                "pre_news_end": pre_news_end
            }
        
        # Resolve the per-ticker price table (convention: historical_data_<lower>) —
        # the SAME name table_exists() checks above. Querying `FROM {ticker}` hit a
        # non-existent relation (e.g. "TSLA") and crashed the pre-news check, which
        # fail-closed and dropped EVERY signal for tickers that actually have data.
        table_name = f"historical_data_{ticker.lower()}"

        # Get pre-news data
        pre_news_query = f"""
            SELECT timestamp, open, high, low, close, volume
            FROM {table_name}
            WHERE timestamp >= %s AND timestamp < %s
            ORDER BY timestamp ASC
        """
        
        with db_manager.get_connection() as conn:
            pre_news_data = pd.read_sql(
                pre_news_query,
                conn,
                params=(pre_news_start, pre_news_end)
            )
        
        if pre_news_data.empty or len(pre_news_data) < 2:
            return {
                "suspicious": False,
                "has_data": False,
                "price_change_pct": 0.0,
                "volume_change_pct": 0.0,
                "flags": ["Insufficient data in pre-news window"],
                "pre_news_start": pre_news_start,
                "pre_news_end": pre_news_end
            }
        
        # Calculate price change in pre-news window
        first_price = pre_news_data.iloc[0]['close']
        last_price = pre_news_data.iloc[-1]['close']
        price_change_pct = ((last_price - first_price) / first_price) * 100
        
        # Check for significant price movement (>2%)
        if abs(price_change_pct) > 2.0:
            flags.append(f"Price moved {price_change_pct:+.2f}% before news")
            suspicious = True
        
        # Get baseline data for volume comparison
        baseline_query = f"""
            SELECT timestamp, volume
            FROM {table_name}
            WHERE timestamp >= %s AND timestamp < %s
            ORDER BY timestamp ASC
        """
        
        with db_manager.get_connection() as conn:
            baseline_data = pd.read_sql(
                baseline_query,
                conn,
                params=(baseline_start, baseline_end)
            )
        
        # Calculate volume changes
        volume_change_pct = 0.0
        if not baseline_data.empty and baseline_data['volume'].sum() > 0:
            pre_news_volume = pre_news_data['volume'].sum()
            baseline_volume = baseline_data['volume'].sum()
            volume_change_pct = ((pre_news_volume - baseline_volume) / baseline_volume) * 100
            
            # Check for unusual volume spike (>50% increase)
            if volume_change_pct > 50.0:
                flags.append(f"Volume spiked {volume_change_pct:+.1f}% before news")
                suspicious = True
        
        # Check for both price and volume anomalies (strong signal)
        if abs(price_change_pct) > 1.5 and volume_change_pct > 30.0:
            flags.append("Both price and volume anomalies detected")
            suspicious = True
        
        # Check for extreme movements (>3%)
        if abs(price_change_pct) > 3.0:
            flags.append(f"EXTREME price movement: {price_change_pct:+.2f}%")
            suspicious = True
        
        return {
            "suspicious": suspicious,
            "has_data": True,
            "price_change_pct": float(price_change_pct),
            "volume_change_pct": float(volume_change_pct),
            "flags": flags if flags else ["No suspicious activity detected"],
            "pre_news_start": pre_news_start,
            "pre_news_end": pre_news_end,
            "first_price": float(first_price),
            "last_price": float(last_price)
        }
        
    except Exception as e:
        print(f"Error checking pre-news movement for {ticker}: {e}")
        return {
            "suspicious": False,
            "has_data": False,
            "price_change_pct": 0.0,
            "volume_change_pct": 0.0,
            "flags": [f"Error: {str(e)}"],
            "pre_news_start": pre_news_start,
            "pre_news_end": pre_news_end
        }


def check_multiple_tickers_pre_news(
    tickers: list,
    news_timestamp: datetime,
    db_manager: Any,
    window_minutes: int = 60
) -> Dict[str, Dict[str, Any]]:
    """
    Check pre-news movement for multiple tickers
    
    Args:
        tickers: List of ticker symbols
        news_timestamp: When the news was published
        window_minutes: Time window before news to check
        db_manager: Database manager instance
        
    Returns:
        Dict mapping ticker to movement analysis
    """
    results = {}
    
    for ticker in tickers:
        results[ticker] = check_pre_news_movement(
            ticker, news_timestamp, db_manager, window_minutes
        )
    
    return results


def create_get_pre_news_movement_tool(timestamp, db_manager, window_minutes: int = 60):
    """LangChain tool: let the agent inspect price action BEFORE the news.

    Bound to the article timestamp so the model can avoid choosing a direction
    that the market has already moved (a top source of losing trades).
    """
    import json
    from langchain_core.tools import tool

    @tool
    def get_pre_news_movement_tool(ticker: str) -> str:
        """Check how a ticker's price/volume moved in the window BEFORE this news was
        published. Use this before issuing a signal: if the stock already moved strongly
        in the direction your news implies, the catalyst is likely priced in (avoid chasing).
        Returns price_change_pct (pre-news), volume_change_pct, has_data, and flags."""
        res = check_pre_news_movement(ticker, timestamp, db_manager, window_minutes)
        return json.dumps({
            "ticker": ticker.upper(),
            "has_data": res.get("has_data", False),
            "pre_news_price_change_pct": round(res.get("price_change_pct", 0.0), 3),
            "pre_news_volume_change_pct": round(res.get("volume_change_pct", 0.0), 1),
            "suspicious": res.get("suspicious", False),
            "flags": res.get("flags", []),
        }, default=str)

    return get_pre_news_movement_tool

