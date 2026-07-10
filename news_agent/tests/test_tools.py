"""
Integration tests for all non-graph agent tools.
Tests use real DB data auto-discovered by conftest fixtures.
Assertions are structural: correct keys, correct types, sane values.
"""
import pandas as pd
import pytest

from news_agent.tools.ticker_extraction_tool import validate_ticker
from news_agent.tools.sentiment_analysis_tool import get_sentiment_template
from news_agent.tools.db_query_tool import get_historical_prices, get_price_at_time
from news_agent.tools.get_recent_news import get_recent_news_items
from news_agent.tools.get_news_by_id import get_news_with_price_impact
from news_agent.tools.technical_analysis_tool import calculate_technical_indicators
from news_agent.tools.volatility_volume_detector_tool import detect_volatility_and_volume
from news_agent.tools.risk_assessment_tool import assess_risk
from news_agent.tools.sector_competitor_tool import analyze_sector_and_competitors
from news_agent.tools.find_similar_news_tool import find_similar_news
from news_agent.tools.pre_news_movement_tool import check_pre_news_movement


# ---------------------------------------------------------------------------
# Ticker validation (no DB, uses Yahoo Finance)
# ---------------------------------------------------------------------------

def test_validate_valid_ticker():
    result = validate_ticker("AAPL")
    assert isinstance(result, dict)
    assert result["valid"] is True
    assert result["ticker"] == "AAPL"


def test_validate_invalid_ticker():
    result = validate_ticker("INVALIDXXX999")
    assert isinstance(result, dict)
    assert result["valid"] is False


# ---------------------------------------------------------------------------
# Sentiment template (pure function)
# ---------------------------------------------------------------------------

def test_sentiment_template():
    tmpl = get_sentiment_template()
    assert isinstance(tmpl, dict)
    for key in ("ticker", "sentiment", "intensity", "reasoning", "key_phrases"):
        assert key in tmpl
    assert tmpl["sentiment"] in ("bullish", "bearish", "neutral")
    assert 0 <= tmpl["intensity"] <= 100
    assert isinstance(tmpl["key_phrases"], list)


# ---------------------------------------------------------------------------
# DB price tools
# ---------------------------------------------------------------------------

def test_get_price_at_time(db_manager, known_ticker, past_timestamp):
    result = get_price_at_time(known_ticker, past_timestamp, db_manager)
    assert result is not None, "Expected price data, got None"
    assert isinstance(result, dict)
    for key in ("date", "open", "high", "low", "close", "volume"):
        assert key in result, f"Missing key: {key}"
    assert result["close"] > 0
    assert result["high"] >= result["low"]
    assert result["volume"] >= 0


def test_get_historical_prices(db_manager, known_ticker, past_timestamp):
    df = get_historical_prices(known_ticker, past_timestamp, lookback_days=5, db_manager=db_manager)
    assert df is not None, "Expected DataFrame, got None"
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    for col in ("date", "open", "high", "low", "close", "volume"):
        assert col in df.columns, f"Missing column: {col}"
    assert (df["close"] > 0).all()


# ---------------------------------------------------------------------------
# News tools
# ---------------------------------------------------------------------------

def test_get_recent_news_items(db_manager, past_timestamp):
    items = get_recent_news_items(past_timestamp, db_manager, hours=24 * 30)
    assert isinstance(items, list)
    if items:
        item = items[0]
        for key in ("id", "title", "published"):
            assert key in item, f"Missing key: {key}"
        assert item["id"] is not None
        assert isinstance(item["title"], str)


def test_get_news_by_id(db_manager, known_news_id):
    result = get_news_with_price_impact(known_news_id, db_manager)
    assert result is not None, "Expected news dict, got None"
    assert isinstance(result, dict)
    for key in ("id", "title"):
        assert key in result, f"Missing key: {key}"
    assert result["id"] == known_news_id


# ---------------------------------------------------------------------------
# Technical analysis
# ---------------------------------------------------------------------------

def test_calculate_technical_indicators(db_manager, known_ticker, past_timestamp):
    price_data = get_historical_prices(known_ticker, past_timestamp, lookback_days=30, db_manager=db_manager)
    if price_data is None:
        pytest.skip(f"No 30-day price data for {known_ticker}")
    result = calculate_technical_indicators(known_ticker, price_data)
    assert isinstance(result, dict)
    assert "error" not in result, f"Tool returned error: {result.get('error')}"
    for key in ("current_price", "rsi", "atr", "trend"):
        assert key in result, f"Missing key: {key}"
    assert result["current_price"] > 0
    assert 0 <= result["rsi"] <= 100
    assert result["trend"] in ("bullish", "bearish", "neutral")


# ---------------------------------------------------------------------------
# Volatility / volume detector
# ---------------------------------------------------------------------------

def test_detect_volatility_and_volume(db_manager, known_ticker, past_timestamp):
    result = detect_volatility_and_volume(known_ticker, past_timestamp, db_manager)
    assert isinstance(result, dict)
    assert "ticker" in result
    assert result["ticker"] == known_ticker
    for key in ("volatility_detected", "volume_spike_detected", "is_hot_stock"):
        assert key in result, f"Missing key: {key}"
    assert result["is_hot_stock"] in (True, False)


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------

def test_assess_risk(db_manager, known_ticker, past_timestamp):
    price_data = get_historical_prices(known_ticker, past_timestamp, lookback_days=30, db_manager=db_manager)
    if price_data is None:
        pytest.skip(f"No price data for {known_ticker}")
    tech = calculate_technical_indicators(known_ticker, price_data)
    sentiment = {"ticker": known_ticker, "sentiment": "bullish", "intensity": 60, "reasoning": "test", "key_phrases": []}
    result = assess_risk(
        ticker=known_ticker,
        timestamp=past_timestamp,
        db_manager=db_manager,
        sentiment_data=sentiment,
        technical_indicators=tech,
    )
    assert isinstance(result, dict)
    for key in ("risk_score", "risk_level", "recommendation"):
        assert key in result, f"Missing key: {key}"
    assert 0 <= result["risk_score"] <= 100
    assert result["risk_level"] in ("low", "medium", "high", "extreme")


# ---------------------------------------------------------------------------
# Sector analysis
# ---------------------------------------------------------------------------

def test_analyze_sector_and_competitors(db_manager, past_timestamp):
    result = analyze_sector_and_competitors("AAPL", past_timestamp, db_manager)
    assert isinstance(result, dict)
    for key in ("ticker", "sector", "competitors"):
        assert key in result, f"Missing key: {key}"
    assert result["ticker"] == "AAPL"
    assert isinstance(result["competitors"], list)


# ---------------------------------------------------------------------------
# Similar news (vector search — may return empty list if embeddings absent)
# ---------------------------------------------------------------------------

def test_find_similar_news(db_manager, past_timestamp):
    results = find_similar_news(
        "Apple announces new chip breakthrough",
        past_timestamp,
        db_manager,
        top_k=3,
    )
    assert isinstance(results, list)
    for item in results:
        assert "id" in item
        assert "similarity" in item
        assert 0.0 <= item["similarity"] <= 1.0


# ---------------------------------------------------------------------------
# Pre-news movement
# ---------------------------------------------------------------------------

def test_check_pre_news_movement(db_manager, known_ticker, past_timestamp):
    result = check_pre_news_movement(known_ticker, past_timestamp, db_manager)
    assert isinstance(result, dict)
    for key in ("suspicious", "price_change_pct", "flags"):
        assert key in result, f"Missing key: {key}"
    assert isinstance(result["suspicious"], bool)
    assert isinstance(result["flags"], list)
