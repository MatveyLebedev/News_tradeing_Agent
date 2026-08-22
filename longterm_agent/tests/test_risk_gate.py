"""
Integration tests for the risk_gate deterministic guards:
  * data-sufficiency guard (the JPM fix): no usable income fundamentals → capped/filtered.
  * no-shift gate: with no detected background shift, conviction is capped below the trade
    threshold (deterministic not-traded), so no-shift "trades" can't appear on LLM jitter.
Needs the DB (priced_in_check / earnings_event_risk read it); skips if unreachable.
"""
from datetime import datetime

from longterm_agent.agent.nodes import risk_gate_node


def _state(conviction, fund, has_shifted=True):
    # default: a shift IS present, so these tests isolate the *fundamentals* guard.
    shift = {"has_shifted": has_shifted, "direction": "positive"} if has_shifted else {"has_shifted": False}
    return {"ticker": "TESTX", "as_of": datetime(2026, 6, 15), "article_count": 300,
            "signal": {"direction": "LONG", "conviction": conviction},
            "fundamentals": fund, "background_shift": shift}


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


def test_usable_fundamentals_with_shift_passes(db, cfg):
    # real income metrics present AND a shift present → neither guard caps; high conviction passes.
    # `verdict` is explicit because the state must also be a licensed matrix cell (positive
    # shift + confirms → LONG); without it the matrix-cell veto caps, which is a different guard.
    fund = {"has_statements": True, "revenue_yoy": 0.5, "trend": {"verdict": "confirms"},
            "margin_series": [{"gross_margin": 0.6, "operating_margin": 0.4, "net_margin": 0.3}]}
    out = risk_gate_node(_state(0.8, fund), db, cfg)
    assert not any("no usable income fundamentals" in f for f in out["risk_flags"])
    assert not any("no background shift" in f for f in out["risk_flags"])
    assert out["signal"]["conviction"] >= cfg.LONGTERM_CONFIDENCE_THRESHOLD


def test_no_shift_caps_conviction(db, cfg):
    # usable fundamentals but NO shift → no-shift gate caps below threshold → not traded
    fund = {"has_statements": True, "revenue_yoy": 0.85,
            "margin_series": [{"gross_margin": 0.7, "operating_margin": 0.5, "net_margin": 0.4}]}
    out = risk_gate_node(_state(0.9, fund, has_shifted=False), db, cfg)
    assert out["signal"]["conviction"] < cfg.LONGTERM_CONFIDENCE_THRESHOLD
    assert out["filtered_signal"] is None
    assert any("no background shift" in f for f in out["risk_flags"])


def test_no_shift_gate_can_be_disabled(db, cfg, monkeypatch):
    # with REQUIRE_SHIFT_TO_TRADE off, a no-shift high-conviction signal is NOT capped by this gate
    monkeypatch.setattr(cfg, "REQUIRE_SHIFT_TO_TRADE", False)
    fund = {"has_statements": True, "revenue_yoy": 0.85,
            "margin_series": [{"gross_margin": 0.7, "operating_margin": 0.5, "net_margin": 0.4}]}
    out = risk_gate_node(_state(0.9, fund, has_shifted=False), db, cfg)
    assert not any("no background shift" in f for f in out["risk_flags"])


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
