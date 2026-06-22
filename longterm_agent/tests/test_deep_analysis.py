"""Phase 7A deep_analysis loop tests — fake LLM, no network.

Verifies the bounded ReAct loop end to end: tool execution, result truncation, forced
finalization, the enable/no-shift gates, and the hard determinism boundary (the node never
mutates background_shift). See DEEP_ANALYSIS_PLAN.md §7.5.
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from longterm_agent.agent import nodes


@tool
def probe() -> str:
    """A probe tool returning a large blob (to exercise truncation)."""
    return "X" * 5000


class FakeLLM:
    """Emits one tool call, then finalizes to a DeepAnalysisOutput. Records the messages it
    last saw so the test can inspect the (truncated) ToolMessage."""
    def __init__(self):
        self.invocations = 0
        self.seen = []

    def bind_tools(self, tools):
        self._tools = tools
        return self

    def invoke(self, messages):
        self.seen = list(messages)
        self.invocations += 1
        if self.invocations == 1:
            return AIMessage(content="", tool_calls=[{"name": "probe", "args": {}, "id": "c1"}])
        return AIMessage(content="ready")

    def with_structured_output(self, schema, **kwargs):
        def _invoke(messages):
            return schema(findings=["sector-wide selloff"], is_sector_wide=True,
                          competitor_read="peers down", catalyst_assessment="new catalyst",
                          analog_takeaway="supports long", conviction_adjustment=0.1,
                          tools_used=[])
        return SimpleNamespace(invoke=_invoke)


def _cfg(**over):
    base = dict(DEEP_ANALYSIS_ENABLED=True, MAX_TOOL_CALL_ITERATIONS=3,
                TOOL_RESULT_MAX_CHARS=100, AGENT_VERSION="test",
                LOOKBACK_DAYS_TECHNICAL_LT=180, LOOKBACK_DAYS_BACKGROUND=60,
                AFFECTS_MIN_CONFIDENCE=0.5)
    base.update(over)
    return SimpleNamespace(**base)


def _state():
    return {
        "ticker": "NVDA", "as_of": datetime(2026, 6, 21),
        "background_shift": {"has_shifted": True, "direction": "positive", "magnitude_z": 1.6,
                             "shift_score": 0.8, "candidate_catalysts": []},
        "background_metrics": {"new_stories": []},
        "fundamentals": {"trend": {"verdict": "confirms"}},
        "shift_analogs": {"n_analogs": 0},
    }


def test_deep_analysis_enriches_and_preserves_shift(monkeypatch):
    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools",
                        lambda *a, **k: [probe])
    llm = FakeLLM()
    state = _state()
    shift_before = dict(state["background_shift"])

    out = nodes.deep_analysis_node(state, llm, db_manager=None, neo4j_manager=None, config=_cfg())

    enr = out["enriched_analysis"]
    assert enr["conviction_adjustment"] == 0.1
    assert enr["is_sector_wide"] is True
    assert "probe" in enr["tools_used"]            # backfilled from the executed loop
    # the tool ran and its result was truncated to TOOL_RESULT_MAX_CHARS (+ marker)
    tmsgs = [m for m in llm.seen if isinstance(m, ToolMessage)]
    assert tmsgs and all(len(m.content) <= 115 for m in tmsgs)
    # HARD determinism boundary: the deterministic shift label is untouched
    assert out["background_shift"] == shift_before


def test_disabled_is_noop(monkeypatch):
    called = {"built": False}

    def _build(*a, **k):
        called["built"] = True
        return [probe]

    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools", _build)
    out = nodes.deep_analysis_node(_state(), FakeLLM(), None, None, _cfg(DEEP_ANALYSIS_ENABLED=False))
    assert "enriched_analysis" not in out
    assert called["built"] is False                 # never even builds tools when disabled


def test_no_shift_skips(monkeypatch):
    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools",
                        lambda *a, **k: [probe])
    state = _state()
    state["background_shift"] = {"has_shifted": False}
    out = nodes.deep_analysis_node(state, FakeLLM(), None, None, _cfg())
    assert "enriched_analysis" not in out


def test_tools_smoke():
    """build_deep_analysis_tools wires real @tools without touching the network (neo4j=None)."""
    from longterm_agent.tools.analysis_tools import build_deep_analysis_tools
    tools = build_deep_analysis_tools("NVDA", datetime(2026, 6, 21), db_manager=None,
                                      neo4j_manager=None, config=_cfg(), shift={}, metrics={})
    names = {t.name for t in tools}
    assert {"get_fundamentals_detail", "get_long_term_technicals",
            "search_company_news", "find_historical_analogs"} <= names
