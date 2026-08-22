"""P2a — the risk gate records its own arithmetic (docs/AB_PLAN_selection_stage.md).

The contract that makes gate A/Bs replayable offline: from `gate_trace` alone you can
reconstruct `conviction_final` from `conviction_raw`, and see WHY a signal was rejected.
Pure tests — a stub db_manager, no DB and no LLM.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from longterm_agent.agent import nodes
from longterm_agent.config import LongTermAgentConfig

AS_OF = datetime(2026, 5, 4, 6, 30)


@pytest.fixture
def stub(monkeypatch):
    """Neutral risk readers: no priced-in move, no earnings, no regime."""
    monkeypatch.setattr(nodes, "priced_in_check",
                        lambda *a, **k: {"available": True, "window_move_pct": 0.0})
    monkeypatch.setattr(nodes, "earnings_event_risk", lambda *a, **k: {"flag": False})
    monkeypatch.setattr(nodes, "regime_context", lambda *a, **k: {"available": False})


def _state(**over):
    st = {
        "ticker": "NVDA", "as_of": AS_OF, "article_count": 999,
        "signal": {"direction": "LONG", "conviction": 0.80},
        # `direction` carries the shift SIGN: with verdict=confirms it is what makes a LONG
        # a licensed matrix cell, so these tests exercise the trace rather than tripping
        # the matrix-cell veto. Tests that flip the signal side override it.
        "background_shift": {"has_shifted": True, "shift_score": 0.75, "direction": "positive"},
        # shape per _has_usable_fundamentals: income-side data, not just balance-sheet rows
        "fundamentals": {"trend": {"verdict": "confirms"}, "has_statements": True,
                         "revenue_yoy": 12.0, "margin_series": [{"net_margin": 0.3}]},
        "technical_longterm": {"return_window_pct": 5.0},
    }
    st.update(over)
    return st


def _run(state, cfg=None):
    return nodes.risk_gate_node(state, SimpleNamespace(db_config={}), cfg or LongTermAgentConfig)


def _reconstruct(trace):
    """conviction_final from the trace alone — the property the offline replay needs."""
    c = trace["conviction_raw"]
    if trace["gate_mode"] == "table":
        c = round(min(1.0, max(0.0, trace["table_base"] + trace["deep_nudge"])), 2)
    if trace["vetoes"]:
        c = round(max(0.0, min(c, trace["threshold"] - 0.05)), 2)
    for amount in trace["penalties"].values():
        c = round(max(0.0, c + amount), 2)
    return c


def test_trace_reconstructs_final_conviction(stub):
    d = _run(_state())
    t = d["gate_trace"]
    assert t["conviction_raw"] == 0.80
    assert t["conviction_final"] == pytest.approx(_reconstruct(t))
    assert t["conviction_final"] == d["signal"]["conviction"]
    assert t["passed"] is True and t["vetoes"] == [] and t["penalties"] == {}


def test_no_shift_is_recorded_as_a_veto(stub):
    d = _run(_state(background_shift={"has_shifted": False, "shift_score": 0.1}))
    t = d["gate_trace"]
    assert "no_shift" in t["vetoes"]
    assert t["passed"] is False
    assert t["conviction_final"] == pytest.approx(_reconstruct(t))


def test_missing_fundamentals_is_recorded_as_a_veto(stub):
    d = _run(_state(fundamentals={"trend": {"verdict": "unknown"}}))
    t = d["gate_trace"]
    assert "no_usable_fundamentals" in t["vetoes"]
    assert t["conviction_final"] == pytest.approx(_reconstruct(t))


def test_penalties_are_recorded_with_their_amounts(stub, monkeypatch):
    # priced-in fires (move WITH a LONG) and earnings fires
    monkeypatch.setattr(nodes, "priced_in_check",
                        lambda *a, **k: {"available": True, "window_move_pct": 25.0})
    monkeypatch.setattr(nodes, "earnings_event_risk",
                        lambda *a, **k: {"flag": True, "upcoming": "2026-05-10"})
    t = _run(_state())["gate_trace"]
    assert t["penalties"]["priced_in"] == -LongTermAgentConfig.PRICED_IN_PENALTY
    assert t["penalties"]["earnings"] == -LongTermAgentConfig.EARNINGS_PENALTY
    assert t["conviction_final"] == pytest.approx(_reconstruct(t))


def test_counter_trend_penalty_recorded(stub):
    # SHORT into a +80% trailing move → the 180d guard fires
    t = _run(_state(signal={"direction": "SHORT", "conviction": 0.80},
                    background_shift={"has_shifted": True, "shift_score": 0.75,
                                      "direction": "negative"},
                    technical_longterm={"return_window_pct": 80.0}))["gate_trace"]
    assert t["penalties"]["counter_trend"] == -LongTermAgentConfig.COUNTER_TREND_PENALTY
    assert t["trailing_return_pct"] == 80.0
    assert t["conviction_final"] == pytest.approx(_reconstruct(t))


def test_table_inputs_stamped_even_in_llm_mode(stub):
    """`gate=table` replays need these, and they are unrecoverable from a stored row."""
    t = _run(_state())["gate_trace"]
    assert t["gate_mode"] == "llm"
    assert t["table_inputs"] == {"shift_score": 0.75, "verdict": "confirms",
                                "shift_direction": "positive"}
    assert "deep_nudge" in t


def test_adverse_momentum_input_is_available_for_the_pending_ab(stub, monkeypatch):
    """`adv_mom`'s stage-1 replay needs the window move even when no penalty fired."""
    monkeypatch.setattr(nodes, "priced_in_check",
                        lambda *a, **k: {"available": True, "window_move_pct": 34.9})
    t = _run(_state(signal={"direction": "SHORT", "conviction": 0.80},
                    background_shift={"has_shifted": True, "shift_score": 0.75,
                                      "direction": "negative"}))["gate_trace"]
    assert t["window_move_pct"] == 34.9        # move AGAINST the short
    assert t["penalties"] == {}                # nothing fires today — that is the finding
