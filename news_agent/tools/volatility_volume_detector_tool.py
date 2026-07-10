"""
Tool for detecting high volatility and volume spikes

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from typing import Dict, Any
from datetime import datetime


def detect_volatility_and_volume(
    ticker: str,
    timestamp: datetime,
    db_manager: Any,
    lookback_days: int = 1,
    comparison_days: int = 30,
    volume_spike_multiplier: float = 2.0
) -> Dict[str, Any]:
    """
    Detect high volatility and volume spikes in the past day
    Helps identify if stock is already "hot" from previous news
    
    Args:
        ticker: Stock ticker symbol
        timestamp: Reference timestamp
        db_manager: Database manager instance
        lookback_days: Days to analyze for recent volatility (default: 1)
        comparison_days: Days for historical average comparison (default: 30)
        volume_spike_multiplier: Multiplier for volume spike detection (default: 2.0)
        
    Returns:
        Dict with volatility and volume analysis
    """
    
    try:
        # Get recent price data (past day)
        recent_data = db_manager.get_historical_prices(ticker, timestamp, lookback_days)
        
        # Get historical data for comparison (past 30 days)
        historical_data = db_manager.get_historical_prices(ticker, timestamp, comparison_days)
        
        if recent_data is None or historical_data is None or len(historical_data) < 5:
            print(f"Insufficient data for volatility analysis: {ticker}")
            return {
                'ticker': ticker,
                'volatility_detected': False,
                'volume_spike_detected': False,
                'error': 'Insufficient data'
            }
        
        # Calculate recent volatility (past day)
        recent_data = recent_data.copy()
        recent_data['volatility'] = (recent_data['high'] - recent_data['low']) / recent_data['open'] * 100
        recent_volatility = recent_data['volatility'].mean()
        
        # Calculate historical average volatility
        historical_data = historical_data.copy()
        historical_data['volatility'] = (historical_data['high'] - historical_data['low']) / historical_data['open'] * 100
        avg_volatility = historical_data['volatility'].mean()
        
        # Check if recent volatility is higher than average
        volatility_ratio = recent_volatility / avg_volatility if avg_volatility > 0 else 1.0
        high_volatility = volatility_ratio > 1.5
        
        # Calculate recent average volume
        recent_avg_volume = recent_data['volume'].mean()
        
        # Calculate historical average volume
        historical_avg_volume = historical_data['volume'].mean()
        
        # Check for volume spike
        volume_ratio = recent_avg_volume / historical_avg_volume if historical_avg_volume > 0 else 1.0
        volume_spike = volume_ratio >= volume_spike_multiplier
        
        # Determine likely cause
        likely_cause = "normal trading"
        if high_volatility and volume_spike:
            likely_cause = "previous news effect - high volatility with volume support"
        elif high_volatility and not volume_spike:
            likely_cause = "low-volume volatility - unreliable signal"
        elif volume_spike and not high_volatility:
            likely_cause = "volume spike without volatility - accumulation/distribution"
        
        # Calculate volatility score (0-100)
        volatility_score = min(100, int(volatility_ratio * 50))
        
        result = {
            'ticker': ticker,
            'volatility_detected': high_volatility,
            'volume_spike_detected': volume_spike,
            'recent_volatility_pct': float(recent_volatility),
            'avg_volatility_pct': float(avg_volatility),
            'volatility_ratio': float(volatility_ratio),
            'recent_avg_volume': int(recent_avg_volume),
            'historical_avg_volume': int(historical_avg_volume),
            'volume_ratio': float(volume_ratio),
            'volatility_score': volatility_score,
            'likely_cause': likely_cause,
            'is_hot_stock': high_volatility and volume_spike
        }
        
        print(f"✓ Volatility analysis for {ticker}: "
              f"Volatility={high_volatility}, Volume Spike={volume_spike}")
        
        return result
        
    except Exception as e:
        print(f"Error detecting volatility for {ticker}: {e}")
        return {
            'ticker': ticker,
            'volatility_detected': False,
            'volume_spike_detected': False,
            'error': str(e)
        }



