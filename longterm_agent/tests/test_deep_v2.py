"""Phase 6 deep-analysis v2 tests — planner, parallel cited threads, citation guard.

Fake LLMs, no network. v1 invariance is covered by test_deep_analysis.py (the flag
defaults off); here we assert the v2 branch behavior and the corpus-reuse tool path.
"""
import json
from datetime import datetime
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from longterm_agent.agent import nodes


@tool
def probe() -> str:
    """A probe tool used by the fake threads."""
    return "peer AVGO guided down; see news_id 42"


class FakeLLMV2:
    """Planner → 3 threads; map threads → one probe call, then a CITED finding; reduce →
    DeepAnalysisOutput. Schema-aware like the v1 fakes."""
    def __init__(self, plan_threads=None, cite=True):
        self.plan_threads = plan_threads
        self.cite = cite
        self.planner_prompts = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if not any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="", tool_calls=[{"name": "probe", "args": {}, "id": "c1"}])
        return AIMessage(content="done")

    def with_structured_output(self, schema, **kwargs):
        def _invoke(prompt):
            if schema.__name__ == "DeepAnalysisPlan":
                self.planner_prompts.append(str(prompt))
                if self.plan_threads is None:
                    raise RuntimeError("planner down")
                return schema(threads=self.plan_threads, rationale="test plan")
            if schema.__name__ == "DeepAnalysisFindingV2":
                ev = ([{"news_id": 42, "quote": "guided down"}] if self.cite else [])
                return schema(finding="peer guided down", signal="sector-wide", evidence=ev)
            return schema(findings=["f1"], is_sector_wide=True, summary="insight",
                          conviction_adjustment=0.1, tools_used=[])
        return SimpleNamespace(invoke=_invoke)


def _cfg(**over):
    base = dict(DEEP_ANALYSIS_ENABLED=True, DEEP_V2=True, DEEP_LLM_CONCURRENCY=8,
                AGENT_VERSION="test", DETECTION_VERSION="test", MIN_ANALOGS=5,
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
        "news_corpus": [{"id": 42, "published": datetime(2026, 6, 20),
                         "title": "AVGO guides down", "summary": "chip demand"}],
    }


def _patch(monkeypatch):
    monkeypatch.setattr("longterm_agent.tools.analysis_tools.build_deep_analysis_tools",
                        lambda *a, **k: [probe])
    monkeypatch.setattr("longterm_agent.oplog.log", lambda *a, **k: True)


def test_v2_planner_selects_threads_and_findings_cited(monkeypatch):
    _patch(monkeypatch)
    plan = [{"name": "sector_breadth", "focus": "AVGO, AMD"},
            {"name": "legal_regulatory_exposure", "focus": "antitrust probe"},
            {"name": "cause_novelty", "focus": ""}]
    llm = FakeLLMV2(plan_threads=plan)
    state = _state()
    shift_before = dict(state["background_shift"])

    out = nodes.deep_analysis_node(state, llm, FakeLLMV2(plan_threads=plan), None, None, _cfg())

    enr = out["enriched_analysis"]
    assert enr["deep_version"] == 2
    assert [t["name"] for t in enr["planner"]["threads"]] == [
        "sector_breadth", "legal_regulatory_exposure", "cause_novelty"]
    assert enr["planner"]["planner_ok"] is True
    assert len(enr["map_findings"]) == 3                      # all cited → all survive
    assert all(f["evidence"] for f in enr["map_findings"])
    assert enr["conviction_adjustment"] == 0.1                # reduce value kept
    assert "background_shift" not in out                      # delta never carries the shift
    assert state["background_shift"] == shift_before          # input not mutated either
    # planner output is visible in messages
    assert any(m.get("name") == "deep_analysis_planner" for m in out.get("messages", []))


def test_v2_planner_failure_falls_back_to_default_four(monkeypatch):
    _patch(monkeypatch)
    llm = FakeLLMV2(plan_threads=None)  # planner raises
    out = nodes.deep_analysis_node(_state(), llm, FakeLLMV2(plan_threads=None), None, None, _cfg())
    enr = out["enriched_analysis"]
    assert [t["name"] for t in enr["planner"]["threads"]] == [
        "sector_breadth", "cause_novelty", "fundamentals", "analogs"]
    assert enr["planner"]["planner_ok"] is False
    assert enr["map_findings"]                                # fallback still researched


def test_v2_uncited_findings_zero_the_nudge(monkeypatch):
    _patch(monkeypatch)
    plan = [{"name": "fundamentals", "focus": ""}, {"name": "analogs", "focus": ""}]
    llm = FakeLLMV2(plan_threads=plan, cite=False)            # tools ran, but no citations
    out = nodes.deep_analysis_node(_state(), llm, FakeLLMV2(plan_threads=plan), None, None, _cfg())
    enr = out["enriched_analysis"]
    assert enr["tools_used"] == ["probe"]                     # tools DID run
    assert enr["map_findings"] == []                          # …but nothing survived
    assert enr["conviction_adjustment"] == 0.0                # citation guard zeroed it
    assert "citation" in enr.get("note", "")


def test_v2_flag_off_keeps_v1_path(monkeypatch):
    """DEEP_V2 absent/false → the v1 body runs (no planner keys in the output)."""
    _patch(monkeypatch)
    from longterm_agent.tests.test_deep_analysis import FakeLLM
    out = nodes.deep_analysis_node(_state(), FakeLLM(), FakeLLM(), None, None,
                                   _cfg(DEEP_V2=False))
    enr = out["enriched_analysis"]
    assert "planner" not in enr and "deep_version" not in enr


def test_search_company_news_serves_from_state_corpus():
    """With news_corpus passed, the company's own search never hits the DB (db=None would
    crash) and headlines carry citable news_id."""
    from longterm_agent.tools.analysis_tools import build_deep_analysis_tools
    corpus = [{"id": 7, "published": datetime(2026, 6, 1), "title": "NVDA export controls",
               "summary": "chips"},
              {"id": 8, "published": datetime(2026, 6, 2), "title": "NVDA earnings beat",
               "summary": "guidance"}]
    tools = build_deep_analysis_tools(
        "NVDA", datetime(2026, 6, 22), None, None, _cfg(), news_corpus=corpus)
    search = next(t for t in tools if t.name == "search_company_news")
    res = json.loads(search.invoke({"query": "export"}))
    assert res["n"] == 1 and res["headlines"][0]["news_id"] == 7
