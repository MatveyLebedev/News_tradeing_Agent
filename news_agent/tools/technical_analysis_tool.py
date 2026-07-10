"""
Tool for calculating technical indicators

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from typing import Dict, Any
from datetime import datetime
import pandas as pd
from langchain_core.tools import tool
import json


def calculate_technical_indicators(
    ticker: str,
    price_data: pd.DataFrame,
    atr_period: int = 14,
    rsi_period: int = 14,
    ma_short: int = 20,
    ma_long: int = 50,
    bb_period: int = 20,
    bb_std: int = 2
) -> Dict[str, Any]:
    """
    Calculate technical indicators for stop-loss and take-profit decisions
    
    Args:
        ticker: Stock ticker symbol
        price_data: DataFrame with OHLCV data
        atr_period: Period for ATR calculation
        rsi_period: Period for RSI calculation
        ma_short: Short moving average period
        ma_long: Long moving average period
        bb_period: Bollinger Bands period
        bb_std: Bollinger Bands standard deviation
        
    Returns:
        Dict with technical indicators
    """
    
    if price_data is None or price_data.empty:
        print(f"No price data available for {ticker}")
        return {}
    
    try:
        df = price_data.copy()
        
        # Calculate ATR (Average True Range)
        df['tr1'] = abs(df['high'] - df['low'])
        df['tr2'] = abs(df['high'] - df['close'].shift(1))
        df['tr3'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=atr_period).mean().iloc[-1]
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1] if not rsi.empty else 50.0
        
        # Calculate Moving Averages
        ma_short_val = df['close'].rolling(window=min(ma_short, len(df))).mean().iloc[-1]
        ma_long_val = df['close'].rolling(window=min(ma_long, len(df))).mean().iloc[-1] if len(df) >= ma_long else ma_short_val
        
        # Calculate Bollinger Bands
        bb_ma = df['close'].rolling(window=min(bb_period, len(df))).mean()
        bb_std_val = df['close'].rolling(window=min(bb_period, len(df))).std()
        upper_band = bb_ma + (bb_std_val * bb_std)
        lower_band = bb_ma - (bb_std_val * bb_std)
        
        current_price = df['close'].iloc[-1]
        
        # Support and Resistance levels
        recent_data = df.tail(30) if len(df) >= 30 else df
        resistance = recent_data['high'].max()
        support = recent_data['low'].min()
        
        # Calculate distance from support/resistance
        distance_to_resistance = ((resistance - current_price) / current_price) * 100
        distance_to_support = ((current_price - support) / current_price) * 100
        
        indicators = {
            'ticker': ticker,
            'current_price': float(current_price),
            'atr': float(atr) if not pd.isna(atr) else 0.0,
            'rsi': float(current_rsi) if not pd.isna(current_rsi) else 50.0,
            'ma_short': float(ma_short_val) if not pd.isna(ma_short_val) else float(current_price),
            'ma_long': float(ma_long_val) if not pd.isna(ma_long_val) else float(current_price),
            'bollinger_upper': float(upper_band.iloc[-1]) if not pd.isna(upper_band.iloc[-1]) else float(current_price) * 1.02,
            'bollinger_lower': float(lower_band.iloc[-1]) if not pd.isna(lower_band.iloc[-1]) else float(current_price) * 0.98,
            'resistance': float(resistance),
            'support': float(support),
            'distance_to_resistance_pct': float(distance_to_resistance),
            'distance_to_support_pct': float(distance_to_support),
            'trend': determine_trend(ma_short_val, ma_long_val, current_rsi)
        }
        
        print(f"✓ Calculated technical indicators for {ticker}")
        return indicators
        
    except Exception as e:
        print(f"Error calculating technical indicators for {ticker}: {e}")
        return {
            'ticker': ticker,
            'error': str(e)
        }


def determine_trend(ma_short: float, ma_long: float, rsi: float) -> str:
    """
    Determine market trend based on moving averages and RSI
    
    Args:
        ma_short: Short-term moving average
        ma_long: Long-term moving average
        rsi: RSI value
        
    Returns:
        Trend string: 'bullish', 'bearish', or 'neutral'
    """
    if pd.isna(ma_short) or pd.isna(ma_long) or pd.isna(rsi):
        return 'neutral'
    
    # Bullish: short MA > long MA and RSI not overbought
    if ma_short > ma_long and rsi < 70:
        return 'bullish'
    # Bearish: short MA < long MA and RSI not oversold
    elif ma_short < ma_long and rsi > 30:
        return 'bearish'
    else:
        return 'neutral'


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_get_technical_indicators_tool(timestamp: datetime, db_manager: Any):
    """Create get_technical_indicators tool with captured context"""
    
    @tool
    def get_technical_indicators_tool(ticker: str, lookback_days: int = 30) -> str:
        """
        Get technical indicators (RSI, ATR, Moving Averages, Bollinger Bands) for a ticker.
        Use this to analyze technical conditions before making trading decisions.
    
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            lookback_days: Number of days to look back for price data (default: 30)
        
        Returns:
            JSON string with technical indicators including RSI, ATR, MAs, Bollinger Bands, support/resistance
        """
        # Import here to avoid circular dependency
        from .db_query_tool import get_historical_prices
        
        # Get price data first
        price_data = get_historical_prices(
            ticker=ticker,
            timestamp=timestamp,
            lookback_days=lookback_days,
            db_manager=db_manager
        )

        if price_data is None or price_data.empty:
            raise ValueError(f"No price data available for {ticker}. Historical data table may not exist or contains no data.")
        
        # Calculate technical indicators
        indicators = calculate_technical_indicators(
            ticker=ticker,
            price_data=price_data
        )
        
        return json.dumps(indicators, default=str)
    
    return get_technical_indicators_tool

