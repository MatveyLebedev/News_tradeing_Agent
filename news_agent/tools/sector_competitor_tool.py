"""
Tool for analyzing sector and competitor stocks

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from typing import Dict, Any, List, Optional
import yfinance as yf
from datetime import datetime
from langchain_core.tools import tool
import json


def analyze_sector_and_competitors(
    ticker: str,
    timestamp: datetime,
    db_manager: Any,
    max_competitors: int = 3
) -> Dict[str, Any]:
    """
    Analyze sector and competitor stocks using Yahoo Finance
    
    Args:
        ticker: Stock ticker symbol
        timestamp: Reference timestamp for time-aware queries
        db_manager: Database manager instance
        max_competitors: Maximum number of competitors to analyze
        
    Returns:
        Dict with sector info and competitor analysis
    """
    
    try:
        # Get ticker info from Yahoo Finance
        stock = yf.Ticker(ticker)
        info = stock.info
        
        sector = info.get('sector', 'Unknown')
        industry = info.get('industry', 'Unknown')
        
        print(f"✓ {ticker} - Sector: {sector}, Industry: {industry}")
        
        # Get competitors (if available)
        competitors = []
        
        # Try to get related tickers based on industry
        # This is a simplified approach - in production, use a proper industry mapping
        industry_tickers = get_industry_competitors(sector, industry, ticker)
        
        competitor_data = []
        for comp_ticker in industry_tickers[:max_competitors]:
            try:
                # Get competitor price data from DB
                comp_price_data = db_manager.get_price_at_time(comp_ticker, timestamp)
                
                if comp_price_data:
                    # Calculate recent performance
                    comp_hist = db_manager.get_historical_prices(comp_ticker, timestamp, lookback_days=5)
                    
                    if comp_hist is not None and len(comp_hist) >= 2:
                        price_change_pct = (
                            (comp_hist['close'].iloc[-1] - comp_hist['close'].iloc[0]) 
                            / comp_hist['close'].iloc[0] * 100
                        )
                    else:
                        price_change_pct = 0.0
                    
                    competitor_data.append({
                        'ticker': comp_ticker,
                        'current_price': comp_price_data['close'],
                        'price_change_5d_pct': float(price_change_pct)
                    })
            except Exception as e:
                print(f"Could not fetch data for competitor {comp_ticker}: {e}")
                continue
        
        # Analyze sector trend
        sector_trend = "neutral"
        if competitor_data:
            avg_change = sum(c['price_change_5d_pct'] for c in competitor_data) / len(competitor_data)
            if avg_change > 2:
                sector_trend = "bullish"
            elif avg_change < -2:
                sector_trend = "bearish"
        
        result = {
            'ticker': ticker,
            'sector': sector,
            'industry': industry,
            'competitors': competitor_data,
            'sector_trend': sector_trend,
            'sector_avg_change_5d': sum(c['price_change_5d_pct'] for c in competitor_data) / len(competitor_data) if competitor_data else 0.0
        }
        
        print(f"✓ Analyzed {len(competitor_data)} competitors for {ticker}")
        return result
        
    except Exception as e:
        print(f"Error analyzing sector for {ticker}: {e}")
        return {
            'ticker': ticker,
            'sector': 'Unknown',
            'industry': 'Unknown',
            'competitors': [],
            'sector_trend': 'neutral',
            'error': str(e)
        }


def get_industry_competitors(sector: str, industry: str, exclude_ticker: str) -> List[str]:
    """
    Get list of competitor tickers based on sector/industry
    This is a simplified mapping - in production, use a comprehensive database
    
    Args:
        sector: Company sector
        industry: Company industry
        exclude_ticker: Ticker to exclude (the main ticker)
        
    Returns:
        List of competitor tickers
    """
    
    # Industry/sector mapping to tickers (simplified)
    sector_map = {
        'Technology': {
            'Consumer Electronics': ['AAPL', 'MSFT', 'GOOGL', 'META', 'AMZN'],
            'Semiconductors': ['NVDA', 'AMD', 'INTC', 'TSM', 'AVGO'],
            'Software': ['MSFT', 'ORCL', 'CRM', 'ADBE', 'INTU'],
            'default': ['AAPL', 'MSFT', 'GOOGL', 'NVDA', 'META']
        },
        'Financial Services': ['JPM', 'BAC', 'WFC', 'C', 'GS'],
        'Healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV', 'TMO'],
        'Consumer Cyclical': ['AMZN', 'TSLA', 'HD', 'NKE', 'MCD'],
        'Communication Services': ['META', 'GOOGL', 'NFLX', 'DIS', 'CMCSA'],
        'Industrials': ['BA', 'CAT', 'GE', 'HON', 'UPS'],
        'Energy': ['XOM', 'CVX', 'COP', 'SLB', 'EOG'],
        'default': ['SPY', 'QQQ']  # Fallback to market indices
    }
    
    # Get competitors from sector
    if sector in sector_map:
        if isinstance(sector_map[sector], dict):
            # Check if industry is in sub-mapping
            if industry in sector_map[sector]:
                competitors = sector_map[sector][industry]
            else:
                competitors = sector_map[sector].get('default', [])
        else:
            competitors = sector_map[sector]
    else:
        competitors = sector_map['default']
    
    # Exclude the main ticker
    competitors = [t for t in competitors if t != exclude_ticker]
    
    return competitors[:5]  # Return top 5


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_analyze_sector_tool(timestamp: datetime, db_manager: Any):
    """Create analyze_sector tool with captured context"""
    
    @tool
    def analyze_sector_tool(ticker: str, max_competitors: int = 3) -> str:
        """
        Analyze sector and competitor stocks. Use this to understand sector trends and competitor performance.
    
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            max_competitors: Maximum number of competitors to analyze (default: 3)
        
        Returns:
            JSON string with sector info, competitor analysis, and sector trend (bullish/bearish/neutral)
        """
        try:
            results = analyze_sector_and_competitors(
                ticker=ticker,
                timestamp=timestamp,
                db_manager=db_manager,
                max_competitors=max_competitors
            )
            return json.dumps(results, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    return analyze_sector_tool

