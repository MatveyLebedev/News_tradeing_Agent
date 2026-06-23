"""Phase 7A deep_analysis tests — map-reduce, fake LLMs, no network.

Verifies: each map thread runs an isolated bounded loop (tool execution + per-result cap),
the reduce folds findings into DeepAnalysisOutput, the enable/no-shift gates, the no-tools
guard (advisory nudge zeroed), and the hard determinism boundary (background_shift untouched).
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from longterm_agent.agent import nodes
from longterm_agent.agent.nodes import _MAP_TRUNC


@tool
def probe() -> str:
    """A probe tool returning a large blob (to exercise per-result capping)."""
    return "X" * 5000


class FakeLLM:
    """Per map thread: emits one tool call, then finalizes. Schema-aware structured output
    (DeepAnalysisFinding for a map, DeepAnalysisOutput for the reduce)."""
    def __init__(self):
        self.seen = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen = list(messages)
        # one tool call per thread: only while this thread hasn't produced a ToolMessage yet
        if not any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="", tool_calls=[{"name": "probe", "args": {}, "id": "c1"}])
        return AIMessage(content="done")

    def with_structured_output(self, schema, **kwargs):
        def _invoke(_):
            if schema.__name__ == "DeepAnalysisFinding":
                return schema(finding="peer also down 4%", signal="sector-wide")
            return schema(findings=["f1"], is_sector_wide=True, summary="cross-company insight",
                          conviction_adjustment=0.1, tools_used=[])
        return SimpleNamespace(invoke=_invoke)


class FakeLLMNoTools:
    """Never calls tools (reproduces the skip mode) — every map thread finds nothing."""
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="no tools")

    def with_structured_output(self, schema, **kwargs):
        def _invoke(_):
            if schema.__name__ == "DeepAnalysisFinding":
                return schema(finding="", signal="")
            return schema(findings=[], conviction_adjustment=0.1, tools_used=["fabricated"])
        return SimpleNamespace(invoke=_invoke)


def _cfg(**over):
    base = dict(DEEP_ANALYSIS_ENABLED=True, AGENT_VERSION="test",
                LOOKBACK_DAYS_TECHNICAL_LT=180, LOOKBACK_DAYS_BACKGROUND=60,
                AFFECTS_MIN_CONFIDENCE=0.5)
    base.update(over)
    return SimpleNamespace(**base)


def _state():
    return {
        "ticker": "NVDA", "as_of": datetime(2026, 6, 22),
        "background_shift": {"has_shifted": True, "direction": "positive", "magnitude_z": 1.6,
                             "shift_score": 0.8, "candidate_catalysts": []},
        "background_metrics": {"new_stories": []},
        "fundamentals": {"trend": {"verdict": "confirms"}},
        "shift_analogs": {"n_analogs": 0},
    }


def test_map_reduce_enriches_and_preserves_shift(monkeypatch):
    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools",
                        lambda *a, **k: [probe])
    llm_map, llm_reduce = FakeLLM(), FakeLLM()
    state = _state()
    shift_before = dict(state["background_shift"])

    out = nodes.deep_analysis_node(state, llm_map, llm_reduce, None, None, _cfg())

    enr = out["enriched_analysis"]
    assert enr["conviction_adjustment"] == 0.1      # from the reduce
    assert enr["is_sector_wide"] is True
    assert "probe" in enr["tools_used"]             # actually-run tools across threads
    # per-result cap applied inside the map thread (1200), so context stays small
    tmsgs = [m for m in llm_map.seen if isinstance(m, ToolMessage)]
    assert tmsgs and all(len(m.content) <= _MAP_TRUNC for m in tmsgs)
    # HARD determinism boundary: the shift label is untouched
    assert out["background_shift"] == shift_before


def test_disabled_is_noop(monkeypatch):
    called = {"built": False}

    def _build(*a, **k):
        called["built"] = True
        return [probe]

    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools", _build)
    out = nodes.deep_analysis_node(_state(), FakeLLM(), FakeLLM(), None, None,
                                   _cfg(DEEP_ANALYSIS_ENABLED=False))
    assert "enriched_analysis" not in out
    assert called["built"] is False


def test_no_shift_skips(monkeypatch):
    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools",
                        lambda *a, **k: [probe])
    state = _state()
    state["background_shift"] = {"has_shifted": False}
    out = nodes.deep_analysis_node(state, FakeLLM(), FakeLLM(), None, None, _cfg())
    assert "enriched_analysis" not in out


def test_no_tools_called_zeros_adjustment(monkeypatch):
    """If no thread runs a tool, the evidence-free nudge is zeroed and tools_used is empty."""
    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools",
                        lambda *a, **k: [probe])
    out = nodes.deep_analysis_node(_state(), FakeLLMNoTools(), FakeLLM(), None, None, _cfg())
    enr = out["enriched_analysis"]
    assert enr["conviction_adjustment"] == 0.0
    assert enr["tools_used"] == []
    assert "skipped" in enr.get("note", "")


def test_tools_smoke():
    """build_deep_analysis_tools wires real @tools without touching the network (neo4j=None)."""
    from longterm_agent.tools.analysis_tools import build_deep_analysis_tools
    tools = build_deep_analysis_tools("NVDA", datetime(2026, 6, 22), db_manager=None,
                                      neo4j_manager=None, config=_cfg(), shift={}, metrics={})
    names = {t.name for t in tools}
    assert {"get_fundamentals_detail", "get_long_term_technicals",
            "search_company_news", "find_historical_analogs"} <= names
