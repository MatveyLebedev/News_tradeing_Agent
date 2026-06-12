"""
Tool for comprehensive risk assessment

Note: Functions in this module are NOT decorated with @tool.
They are called directly by wrapper functions in nodes.py that have @lc_tool decorator.
"""
from typing import Dict, Any, Optional
from datetime import datetime
from langchain_core.tools import tool
import json

from .volatility_volume_detector_tool import detect_volatility_and_volume


def assess_risk(
    ticker: str,
    timestamp: datetime,
    db_manager: Any,
    sentiment_data: Dict[str, Any],
    technical_indicators: Dict[str, Any],
    sector_analysis: Optional[Dict[str, Any]] = None,
    max_risk_score: int = 100
) -> Dict[str, Any]:
    """
    Comprehensive risk assessment integrating multiple factors
    
    Factors considered:
    - Volatility and volume (using volatility_volume_detector_tool)
    - Market conditions (technical indicators)
    - News sentiment intensity
    - Sector trends
    
    Args:
        ticker: Stock ticker symbol
        timestamp: Reference timestamp
        db_manager: Database manager instance
        sentiment_data: Sentiment analysis results
        technical_indicators: Technical analysis results
        sector_analysis: Optional sector analysis results
        max_risk_score: Maximum risk score (default: 100)
        
    Returns:
        Dict with risk assessment and score (0-100, higher = more risky)
    """
    
    try:
        # Get volatility and volume analysis
        volatility_data = detect_volatility_and_volume(
            ticker=ticker,
            timestamp=timestamp,
            db_manager=db_manager
        )
        
        # Initialize risk components
        risk_components = {}
        
        # 1. Volatility risk (0-30 points)
        volatility_risk = 0
        if volatility_data.get('volatility_detected'):
            volatility_risk = min(30, volatility_data.get('volatility_score', 0) * 0.3)
        risk_components['volatility_risk'] = volatility_risk
        
        # 2. Volume risk (0-20 points)
        volume_risk = 0
        if volatility_data.get('volume_spike_detected'):
            volume_ratio = volatility_data.get('volume_ratio', 1.0)
            volume_risk = min(20, (volume_ratio - 1) * 10)
        risk_components['volume_risk'] = volume_risk
        
        # 3. Technical risk (0-25 points)
        technical_risk = 0
        if technical_indicators:
            rsi = technical_indicators.get('rsi', 50)
            # Overbought (RSI > 70) or oversold (RSI < 30) = higher risk
            if rsi > 70:
                technical_risk += (rsi - 70) * 0.5
            elif rsi < 30:
                technical_risk += (30 - rsi) * 0.5
            
            # Distance to support/resistance
            dist_resistance = abs(technical_indicators.get('distance_to_resistance_pct', 10))
            dist_support = abs(technical_indicators.get('distance_to_support_pct', 10))
            if dist_resistance < 2 or dist_support < 2:
                technical_risk += 10  # Near key levels = higher risk
        
        technical_risk = min(25, technical_risk)
        risk_components['technical_risk'] = technical_risk
        
        # 4. Sentiment risk (0-15 points)
        sentiment_risk = 0
        if sentiment_data:
            intensity = sentiment_data.get('intensity', 50)
            sentiment = sentiment_data.get('sentiment', 'neutral')
            
            # Very high intensity = higher risk (market might overreact)
            if intensity > 80:
                sentiment_risk = 15
            elif intensity > 60:
                sentiment_risk = 10
            elif intensity < 20:
                sentiment_risk = 5  # Low conviction = uncertain
        
        risk_components['sentiment_risk'] = sentiment_risk
        
        # 5. Sector risk (0-10 points)
        sector_risk = 0
        if sector_analysis:
            sector_trend = sector_analysis.get('sector_trend', 'neutral')
            if sector_trend == 'bearish':
                sector_risk = 10
            elif sector_trend == 'bullish':
                sector_risk = 5  # Even bullish sectors have some risk
        
        risk_components['sector_risk'] = sector_risk
        
        # Calculate total risk score
        total_risk = sum(risk_components.values())
        total_risk = min(max_risk_score, total_risk)
        
        # Determine risk level
        if total_risk < 30:
            risk_level = 'low'
        elif total_risk < 60:
            risk_level = 'medium'
        else:
            risk_level = 'high'
        
        # Generate risk factors description
        risk_factors = []
        if volatility_data.get('is_hot_stock'):
            risk_factors.append("Stock already experiencing high volatility with volume support")
        if technical_risk > 15:
            risk_factors.append("Near key technical levels or extreme RSI")
        if sentiment_risk > 10:
            risk_factors.append("High sentiment intensity may lead to overreaction")
        if sector_risk > 5:
            risk_factors.append("Sector showing weakness")
        
        result = {
            'ticker': ticker,
            'risk_score': int(total_risk),
            'risk_level': risk_level,
            'risk_components': {k: round(v, 2) for k, v in risk_components.items()},
            'risk_factors': risk_factors,
            'volatility_data': volatility_data,
            'is_hot_stock': volatility_data.get('is_hot_stock', False),
            'recommendation': generate_risk_recommendation(total_risk, volatility_data)
        }
        
        print(f"✓ Risk assessment for {ticker}: {risk_level} ({int(total_risk)}/100)")
        return result
        
    except Exception as e:
        print(f"Error assessing risk for {ticker}: {e}")
        return {
            'ticker': ticker,
            'risk_score': 50,  # Default medium risk
            'risk_level': 'medium',
            'error': str(e)
        }


def generate_risk_recommendation(risk_score: float, volatility_data: Dict[str, Any]) -> str:
    """
    Generate risk recommendation based on score and volatility
    
    Args:
        risk_score: Total risk score
        volatility_data: Volatility analysis data
        
    Returns:
        Risk recommendation string
    """
    
    if volatility_data.get('is_hot_stock'):
        return "High risk: Stock already hot from previous news. Consider waiting for consolidation."
    
    if risk_score < 30:
        return "Low risk: Good conditions for trade entry with standard position sizing."
    elif risk_score < 60:
        return "Medium risk: Trade with caution. Consider reduced position size."
    else:
        return "High risk: Only take trade with small position size and tight stop-loss."


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_check_risk_tool(timestamp: datetime, db_manager: Any):
    """Create check_risk tool with captured context"""
    
    @tool
    def check_risk_tool(
        ticker: str,
        sentiment_intensity: int,
        sentiment_type: str,
        rsi: float = None,
        atr: float = None
    ) -> str:
        """
        Assess risk for a ticker based on sentiment and technical indicators.
        Use this to evaluate risk before generating trading signals.
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            sentiment_intensity: Sentiment intensity score (0-100)
            sentiment_type: Sentiment type ("bullish", "bearish", or "neutral")
            rsi: Optional RSI value (if available from technical analysis)
            atr: Optional ATR value (if available from technical analysis)
        
        Returns:
            JSON string with risk assessment including risk_score (0-100), risk_level, and risk factors
        """
        try:
            # Build sentiment data
            sentiment_data = {
                "sentiment": sentiment_type,
                "intensity": sentiment_intensity
            }
            
            # Build technical indicators if provided
            technical_indicators = {}
            if rsi is not None:
                technical_indicators["rsi"] = rsi
            if atr is not None:
                technical_indicators["atr"] = atr
            
            # Assess risk
            risk_result = assess_risk(
                ticker=ticker,
                timestamp=timestamp,
                db_manager=db_manager,
                sentiment_data=sentiment_data,
                technical_indicators=technical_indicators,
                sector_analysis=None
            )
            
            return json.dumps(risk_result, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    return check_risk_tool

