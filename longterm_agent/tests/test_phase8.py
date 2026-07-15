"""Phase 8 tests: delta returns, parallel-branch graph structure, idempotent resume."""
from datetime import datetime
from types import SimpleNamespace

from longterm_agent.batch_run_longterm import pending_tickers


# --- per-node deltas -------------------------------------------------------------------
def test_nodes_return_deltas_not_state(monkeypatch):
    """A node's return must contain ONLY the keys it owns (plus messages) and must not
    mutate the input state — the contract the parallel fan-out relies on."""
    from longterm_agent.agent import nodes

    state = {"ticker": "NVDA", "as_of": datetime(2026, 6, 22),
             "background_shift": {"has_shifted": False},
             "background_metrics": {"new_stories": []},
             "messages": [{"name": "earlier"}]}
    before = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in state.items()}

    out = nodes.similar_shift_analysis_node(state, db_manager=None,
                                            config=SimpleNamespace(DETECTION_VERSION="t",
                                                                   MIN_ANALOGS=5))
    assert set(out) == {"shift_analogs"}          # no-shift path: exactly one key
    assert state == before                         # input state untouched
    assert state["messages"] == [{"name": "earlier"}]  # nobody appended in place


def test_reflection_returns_delta_and_bounds_loop():
    from longterm_agent.agent import nodes
    cfg = SimpleNamespace(MAX_REFLECTION_ITERATIONS=0)
    out = nodes.reflection_node({"ticker": "NVDA", "reflection_count": 0}, llm=None, config=cfg)
    assert out == {"reflection_count": 1, "needs_refinement": False}


def test_risk_gate_delta_keys(monkeypatch):
    from longterm_agent.agent import nodes
    monkeypatch.setattr(nodes, "priced_in_check", lambda *a, **k: {"available": False})
    monkeypatch.setattr(nodes, "earnings_event_risk", lambda *a, **k: {"available": False})
    monkeypatch.setattr(nodes, "regime_context", lambda *a, **k: {"available": False})
    cfg = SimpleNamespace(CONVICTION_GATE="llm", LONGTERM_CONFIDENCE_THRESHOLD=0.55,
                          SHIFT_WINDOW_DAYS=14, EARNINGS_RISK_DAYS=7,
                          REQUIRE_SHIFT_TO_TRADE=True, PRICED_IN_MOVE_PCT=8.0,
                          PRICED_IN_PENALTY=0.2, EARNINGS_PENALTY=0.1,
                          COUNTER_TREND_PCT=50.0, COUNTER_TREND_PENALTY=0.2,
                          MIN_ARTICLES_FOR_BACKGROUND=15)
    state = {"ticker": "NVDA", "as_of": datetime(2026, 6, 22),
             "signal": {"direction": "NEUTRAL", "conviction": 0.2},
             "background_shift": {"has_shifted": False},
             "fundamentals": {"has_statements": False}, "article_count": 100}

    out = nodes.risk_gate_node(state, None, cfg)
    assert set(out) == {"risk_flags", "risk_checks", "signal", "filtered_signal", "messages"}
    assert "regime_context" in out["risk_checks"]
    assert state.get("risk_flags") is None        # input not mutated


# --- graph structure -------------------------------------------------------------------
def test_graph_has_parallel_fanout_and_barrier_join():
    from longterm_agent.agent.graph import create_longterm_graph
    compiled = create_longterm_graph(llm_map=None, llm_reduce=None,
                                     config=SimpleNamespace(), db_manager=None)
    edges = {(e.source, e.target) for e in compiled.get_graph().edges}
    assert ("shift_detection", "fundamentals_analysis") in edges
    assert ("shift_detection", "similar_shift_analysis") in edges     # fan-out
    assert ("fundamentals_analysis", "deep_analysis") in edges
    assert ("similar_shift_analysis", "deep_analysis") in edges       # join
    assert ("refine_fanout", "fundamentals_analysis") in edges        # reflection re-fan
    assert ("refine_fanout", "similar_shift_analysis") in edges
    assert ("fundamentals_analysis", "similar_shift_analysis") not in edges  # no chain left


def test_messages_reducer_declared():
    import operator
    from typing import get_type_hints
    from longterm_agent.state import LongTermAgentState
    hints = get_type_hints(LongTermAgentState, include_extras=True)
    meta = getattr(hints["messages"], "__metadata__", ())
    assert operator.add in meta                    # parallel branches merge, not clobber


# --- idempotent batch resume -----------------------------------------------------------
def test_pending_tickers_skip_and_force():
    tickers = ["NVDA", "AAPL", "AMD"]
    done = {"AAPL"}
    assert pending_tickers(tickers, done) == ["NVDA", "AMD"]   # order preserved
    assert pending_tickers(tickers, done, force=True) == tickers
    assert pending_tickers(tickers, set()) == tickers
    assert pending_tickers(["aapl"], {"AAPL"}) == []           # case-normalized
