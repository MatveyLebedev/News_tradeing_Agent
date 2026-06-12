"""
Sentiment analysis utilities
Moved to nodes - this file kept for backward compatibility
"""
from typing import Dict


def get_sentiment_template() -> Dict:
    """
    Get template structure for sentiment analysis
        
    Returns:
        Dict with expected sentiment structure
    """
    return {
        "ticker": "AAPL",
        "sentiment": "bullish",  # bullish, bearish, or neutral
        "intensity": 85,  # 0-100
        "reasoning": "Brief explanation of sentiment",
        "key_phrases": ["phrase 1", "phrase 2", "phrase 3"]
    }
