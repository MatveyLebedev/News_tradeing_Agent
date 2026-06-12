"""
Integration tests for the 5 Neo4j graph tools.
Each tool must return valid JSON (parseable to list) without raising an exception.
Tests are skipped if Neo4j is not available (handled in conftest neo4j_manager fixture).
"""
import json
import pytest

from news_agent.tools.graph_tools import create_graph_tools


@pytest.fixture(scope="module")
def graph_tools(neo4j_manager, db_manager, past_timestamp):
    return create_graph_tools(neo4j_manager, db_manager, past_timestamp)


@pytest.fixture(scope="module")
def tool_map(graph_tools):
    return {t.name: t for t in graph_tools}


def _parse(json_str: str):
    """Assert json_str is a valid JSON list/dict and return it."""
    assert isinstance(json_str, str), "Tool must return a string"
    parsed = json.loads(json_str)
    assert isinstance(parsed, (list, dict))
    return parsed


# ---------------------------------------------------------------------------
# get_events_for_ticker
# ---------------------------------------------------------------------------

def test_get_events_for_ticker(tool_map):
    result = tool_map["get_events_for_ticker"].invoke({"ticker": "AAPL", "days": 30})
    parsed = _parse(result)
    assert isinstance(parsed, list)


def test_get_events_for_ticker_short_window(tool_map):
    result = tool_map["get_events_for_ticker"].invoke({"ticker": "NVDA", "days": 7})
    _parse(result)


# ---------------------------------------------------------------------------
# get_recent_events
# ---------------------------------------------------------------------------

def test_get_recent_events_no_filter(tool_map):
    result = tool_map["get_recent_events"].invoke({"limit": 10})
    parsed = _parse(result)
    assert isinstance(parsed, list)
    for item in parsed:
        assert "id" in item or "name" in item


def test_get_recent_events_with_type(tool_map):
    result = tool_map["get_recent_events"].invoke({"limit": 5, "event_type": "earnings"})
    parsed = _parse(result)
    assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# find_related_events
# ---------------------------------------------------------------------------

def test_find_related_events(tool_map, neo4j_manager, past_timestamp):
    rows = neo4j_manager.query(
        "MATCH (e:Event) WHERE e.date <= datetime($pub) RETURN e.id AS id LIMIT 1",
        {"pub": past_timestamp.isoformat()},
    )
    if not rows:
        pytest.skip("No Event nodes in Neo4j before past_timestamp")
    event_id = rows[0]["id"]
    result = tool_map["find_related_events"].invoke({"event_id": event_id, "depth": 2})
    _parse(result)


# ---------------------------------------------------------------------------
# search_events (requires OPENROUTER_API_KEY + embeddings in DB)
# ---------------------------------------------------------------------------

def test_search_events(tool_map):
    result = tool_map["search_events"].invoke({"query": "Apple chip announcement", "limit": 5})
    parsed = _parse(result)
    assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# get_story_context
# ---------------------------------------------------------------------------

def test_get_story_context_ticker(tool_map):
    result = tool_map["get_story_context"].invoke({"ticker_or_domain": "AAPL"})
    parsed = _parse(result)
    assert isinstance(parsed, list)


def test_get_story_context_domain(tool_map):
    result = tool_map["get_story_context"].invoke({"ticker_or_domain": "macro_markets"})
    parsed = _parse(result)
    assert isinstance(parsed, list)
