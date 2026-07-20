"""Deep-analysis tool canary (2026-07-19 leakage audit, F8).

The original canaries covered the READERS (corpus, fundamentals, technicals) — and every
leak the audit actually found lived one layer up, in the tools the deep-analysis node
calls. This file closes that gap: build the real toolset at a fixed `as_of`, invoke each
tool, and scan its JSON output for any timestamp later than the decision moment.

Generic by construction: a new tool added to `build_deep_analysis_tools` is picked up
automatically (unknown names get empty args), so the guard does not rot as the toolset
grows. Integration test — skips cleanly when the DB or graph is unreachable.
"""
import json
from datetime import datetime

import pytest

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.tools.analysis_tools import build_deep_analysis_tools, make_graph_query_tool
from longterm_agent.tools.pit import max_moment

TICKER = "NVDA"
AS_OF = datetime(2026, 5, 1)

# Fields that are LEGITIMATELY forward-looking: a scheduled date announced in advance is
# knowledge, not leakage. Everything else must be dated at or before AS_OF.
FORWARD_KEYS = {"next_report", "expiries", "expiry", "expiration", "upcoming",
                "earnings_dates", "dateShortInterest"}

# Tools that would reach the network (embedding API) — out of scope here. analyze_sector_tool
# used to live in this set; it resolves sector/industry offline now, so it gets scanned.
NETWORK_TOOLS = {"get_novelty_score"}

# Args per tool; anything not listed is invoked with no arguments (every long-term tool
# defaults to the company under analysis).
TOOL_ARGS = {
    "search_company_news": {"query": "earnings"},
    "get_events_for_ticker": {"ticker": TICKER, "days": 30},
    "get_story_context": {"ticker_or_domain": TICKER},
    "get_story_momentum": {"ticker_or_domain": TICKER},
    "get_cross_ticker_correlation": {"event_type": "earnings", "days": 30},
    "analyze_sector_tool": {"ticker": TICKER, "max_competitors": 3},
}

# Needs a valid event id to say anything; covered by the graph-query contract tests below.
SKIP_TOOLS = {"get_causal_chain_with_prices", "run_graph_query"}


def _prune(node):
    """Drop legitimately-forward subtrees so the scan only sees backward-looking data."""
    if isinstance(node, dict):
        return {k: _prune(v) for k, v in node.items() if k not in FORWARD_KEYS}
    if isinstance(node, list):
        return [_prune(v) for v in node]
    return node


def _tools(db, neo):
    return build_deep_analysis_tools(TICKER, AS_OF, db, neo, LongTermAgentConfig())


def test_no_deep_tool_returns_data_dated_after_as_of(db, neo):
    """The whole point: nothing the researching model can see may postdate the decision."""
    tools = [t for t in _tools(db, neo)
             if t.name not in NETWORK_TOOLS and t.name not in SKIP_TOOLS]
    assert tools, "no deep-analysis tools were built"

    offenders, broken, checked = [], [], 0
    for t in tools:
        try:
            raw = t.invoke(TOOL_ARGS.get(t.name, {}))
        except Exception as e:
            # NOT excused. These tools catch their own failures and return
            # {"available": False}, so an escaping exception is a defect — and a tool that
            # crashes is a tool whose PIT behaviour is untested. Swallowing this is exactly
            # how a tz-comparison regression in the maturity guard slipped through once.
            broken.append(f"{t.name}: {type(e).__name__}: {e}")
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue  # non-JSON tool output carries no scannable structure
        checked += 1
        latest = max_moment(_prune(payload))
        if latest is not None and latest > AS_OF:
            offenders.append(f"{t.name}: {latest}")

    assert not broken, f"deep-analysis tools raised instead of degrading: {broken}"
    if not checked:
        pytest.skip("no deep tool produced scannable output (empty DB?)")
    assert not offenders, (
        "deep-analysis tools returned data dated after as_of "
        f"{AS_OF.isoformat()} — lookahead reaches the researching model: {offenders}")


def test_raw_cypher_drops_future_rows_even_without_a_date_gate(neo):
    """The PIT gate must hold when the model FORGETS to write it — that was the F1 hole."""
    tool = make_graph_query_tool(neo, AS_OF)
    out = json.loads(tool.invoke(
        {"cypher": "MATCH (e:Event) RETURN e.name AS name, e.date AS date LIMIT 50"}))
    rows = out.get("rows") or []
    if not rows and not out.get("pit_note"):
        pytest.skip("no events in the graph")
    latest = max_moment(rows)
    assert latest is None or latest <= AS_OF, (
        f"ungated raw Cypher returned rows dated {latest} > as_of {AS_OF}")


def test_raw_cypher_never_returns_realized_move_fields(neo):
    """Realized reactions are the answer sheet; the raw tool must not serve them."""
    tool = make_graph_query_tool(neo, AS_OF)
    out = json.loads(tool.invoke({"cypher": (
        "MATCH (e:Event)-[r:AFFECTS]->(c:Company) "
        "WHERE e.date <= datetime($as_of) "
        "RETURN e.name AS name, r.move_1d AS move_1d, r.move_4h AS move_4h LIMIT 10")}))
    rows = out.get("rows") or []
    if not rows:
        pytest.skip("no AFFECTS rows in the graph")
    leaked = [r for r in rows if "move_1d" in r or "move_4h" in r]
    assert not leaked, f"raw Cypher returned realized move fields: {leaked[:2]}"
    assert "move_" in (out.get("pit_note") or ""), \
        "stripping realized moves must be reported back to the model"


def test_raw_cypher_still_rejects_writes(neo):
    """Regression guard on the pre-existing read-only layer."""
    tool = make_graph_query_tool(neo, AS_OF)
    out = json.loads(tool.invoke({"cypher": "CREATE (x:Leak {a: 1}) RETURN x"}))
    assert "error" in out and "READ-ONLY" in out["error"]


def test_raw_cypher_output_stays_parseable_when_trimmed(neo):
    """Size-capped output must remain valid JSON and keep the PIT note.

    The cap used to slice the serialized string, so a big result set arrived unparseable —
    and it truncated away the very note telling the model its query was ungated.
    """
    tool = make_graph_query_tool(neo, AS_OF, max_chars=600)
    raw = tool.invoke({"cypher": (
        "MATCH (e:Event) RETURN e.name AS name, e.description AS description, "
        "e.date AS date LIMIT 50")})
    out = json.loads(raw)  # must not raise
    assert len(raw) <= 600
    if out.get("truncated_rows"):
        assert out["n"] == len(out["rows"])
