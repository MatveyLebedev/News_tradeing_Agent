"""`sel_null` — the selection rule and its flag (pure; no DB, no LLM).

The rule lives in ONE function used by both the live gate and the offline A/B, so the
arms cannot drift apart. Default must stay the incumbent (standing rule 4).
"""
from types import SimpleNamespace

import pytest

from longterm_agent.agent import nodes
from longterm_agent.agent.nodes import selection_passed
from longterm_agent.config import LongTermAgentConfig

TH = 0.55


def test_default_is_the_incumbent():
    assert LongTermAgentConfig.SELECTION_MODE == "conviction"
    assert "selection_mode" in LongTermAgentConfig.provenance()["knobs"]


def test_incumbent_thresholds_conviction():
    assert selection_passed("conviction", "LONG", 0.60, [], TH) is True
    assert selection_passed("conviction", "LONG", 0.50, [], TH) is False
    # a veto reaches the incumbent only through the cap it already applied
    assert selection_passed("conviction", "LONG", 0.50, ["no_shift"], TH) is False


def test_vetoes_only_ignores_conviction_but_not_eligibility():
    assert selection_passed("vetoes_only", "LONG", 0.20, [], TH) is True   # low → still trades
    assert selection_passed("vetoes_only", "LONG", 0.95, ["no_shift"], TH) is False
    assert selection_passed("vetoes_only", "SHORT", 0.01, [], TH) is True


def test_neither_arm_trades_a_non_directional_signal():
    for mode in ("conviction", "vetoes_only"):
        assert selection_passed(mode, "NEUTRAL", 0.99, [], TH) is False
        assert selection_passed(mode, "NO_DATA", 0.99, [], TH) is False


def test_none_conviction_does_not_crash_the_incumbent():
    assert selection_passed("conviction", "LONG", None, [], TH) is False
    assert selection_passed("vetoes_only", "LONG", None, [], TH) is True


# --- wired into the gate ---------------------------------------------------------------
def _cfg(mode):
    return SimpleNamespace(
        CONVICTION_GATE="llm", SELECTION_MODE=mode, LONGTERM_CONFIDENCE_THRESHOLD=TH,
        SHIFT_WINDOW_DAYS=14, EARNINGS_RISK_DAYS=7, REQUIRE_SHIFT_TO_TRADE=True,
        PRICED_IN_MOVE_PCT=8.0, PRICED_IN_PENALTY=0.2, EARNINGS_PENALTY=0.1,
        COUNTER_TREND_PCT=50.0, COUNTER_TREND_PENALTY=0.2, MIN_ARTICLES_FOR_BACKGROUND=15,
        CONVICTION_TABLE=LongTermAgentConfig.CONVICTION_TABLE)


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(nodes, "priced_in_check",
                        lambda *a, **k: {"available": True, "window_move_pct": 0.0})
    monkeypatch.setattr(nodes, "earnings_event_risk", lambda *a, **k: {"flag": False})
    monkeypatch.setattr(nodes, "regime_context", lambda *a, **k: {"available": False})


def _state(conviction):
    from datetime import datetime
    return {"ticker": "NVDA", "as_of": datetime(2026, 5, 4, 6, 30), "article_count": 999,
            "signal": {"direction": "LONG", "conviction": conviction},
            "background_shift": {"has_shifted": True, "shift_score": 0.75},
            "fundamentals": {"trend": {"verdict": "confirms"}, "has_statements": True,
                             "revenue_yoy": 12.0, "margin_series": [{"net_margin": 0.3}]},
            "technical_longterm": {"return_window_pct": 5.0}}


def test_gate_honours_the_flag(stub):
    """Same signal, below threshold: filtered by the incumbent, traded by the null."""
    low = _state(0.40)
    inc = nodes.risk_gate_node(low, SimpleNamespace(db_config={}), _cfg("conviction"))
    chal = nodes.risk_gate_node(low, SimpleNamespace(db_config={}), _cfg("vetoes_only"))
    assert inc["filtered_signal"] is None
    assert chal["filtered_signal"] is not None
    assert chal["gate_trace"]["passed"] is True
    assert inc["signal"]["selection"] == "conviction"
    assert chal["signal"]["selection"] == "vetoes_only"


def test_challenger_still_respects_a_veto(stub):
    st = _state(0.90)
    st["background_shift"] = {"has_shifted": False, "shift_score": 0.1}
    out = nodes.risk_gate_node(st, SimpleNamespace(db_config={}), _cfg("vetoes_only"))
    assert "no_shift" in out["gate_trace"]["vetoes"]
    assert out["filtered_signal"] is None


def test_arms_agree_when_conviction_is_high_and_clean(stub):
    st = _state(0.80)
    inc = nodes.risk_gate_node(st, SimpleNamespace(db_config={}), _cfg("conviction"))
    chal = nodes.risk_gate_node(st, SimpleNamespace(db_config={}), _cfg("vetoes_only"))
    assert (inc["filtered_signal"] is not None) and (chal["filtered_signal"] is not None)
