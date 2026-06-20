"""
Integration tests for the risk_gate data-sufficiency guard (the JPM fix).
Needs the DB (priced_in_check / earnings_event_risk read it); skips if unreachable.
"""
from datetime import datetime

from longterm_agent.agent.nodes import risk_gate_node


def _state(conviction, fund):
    return {"ticker": "TESTX", "as_of": datetime(2026, 6, 15), "article_count": 300,
            "signal": {"direction": "LONG", "conviction": conviction},
            "fundamentals": fund, "background_shift": {}}


def test_guard_filters_when_no_usable_fundamentals(db, cfg):
    out = risk_gate_node(_state(0.7, {"has_statements": False}), db, cfg)
    assert out["signal"]["conviction"] < cfg.LONGTERM_CONFIDENCE_THRESHOLD
    assert out["filtered_signal"] is None
    assert any("no usable income fundamentals" in f for f in out["risk_flags"])


def test_guard_filters_bank_case(db, cfg):
    # has_statements True but all-None income → still filtered
    fund = {"has_statements": True, "revenue_yoy": None,
            "margin_series": [{"gross_margin": None, "operating_margin": None, "net_margin": None}]}
    out = risk_gate_node(_state(0.7, fund), db, cfg)
    assert out["filtered_signal"] is None


def test_usable_fundamentals_not_capped(db, cfg):
    # real income metrics present → guard does NOT cap; high conviction passes
    fund = {"has_statements": True, "revenue_yoy": 0.5,
            "margin_series": [{"gross_margin": 0.6, "operating_margin": 0.4, "net_margin": 0.3}]}
    out = risk_gate_node(_state(0.8, fund), db, cfg)
    assert not any("no usable income fundamentals" in f for f in out["risk_flags"])
    # conviction may still drop for priced-in/earnings, but not below threshold from the guard
    assert out["signal"]["conviction"] >= cfg.LONGTERM_CONFIDENCE_THRESHOLD - 0.31


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
