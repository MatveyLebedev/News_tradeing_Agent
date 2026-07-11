"""Phase 7 tests: deterministic conviction gate (table) + counter-trend guard.

Same style as test_risk_gate.py: DB-backed via the shared fixtures (TESTX ticker so
priced-in / earnings lookups find nothing), plus pure tests for the table lookup.
"""
from datetime import datetime

from longterm_agent.agent.nodes import risk_gate_node, _table_conviction
from longterm_agent.config import LongTermAgentConfig

GOOD_FUND = {"has_statements": True, "revenue_yoy": 0.5,
             "margin_series": [{"gross_margin": 0.6, "operating_margin": 0.4, "net_margin": 0.3}],
             "trend": {"verdict": "confirms"}}


def _state(conviction=0.8, direction="LONG", shift_score=0.8, verdict="confirms",
           tech_ret=None, nudge=None):
    fund = dict(GOOD_FUND)
    fund["trend"] = {"verdict": verdict}
    st = {"ticker": "TESTX", "as_of": datetime(2026, 6, 15), "article_count": 300,
          "signal": {"direction": direction, "conviction": conviction},
          "fundamentals": fund,
          "background_shift": {"has_shifted": True, "direction": "positive",
                               "shift_score": shift_score}}
    if tech_ret is not None:
        st["technical_longterm"] = {"trend": "uptrend", "return_window_pct": tech_ret}
    if nudge is not None:
        st["enriched_analysis"] = {"conviction_adjustment": nudge}
    return st


# --- table lookup (pure) --------------------------------------------------------
def test_table_conviction_buckets():
    t = LongTermAgentConfig.CONVICTION_TABLE
    assert _table_conviction(t, 0.2, "confirms") == t["confirms"][0]
    assert _table_conviction(t, 0.55, "confirms") == t["confirms"][1]
    assert _table_conviction(t, 0.9, "confirms") == t["confirms"][2]
    assert _table_conviction(t, 0.9, "contradicts") == t["contradicts"][2]
    # unknown / missing verdicts fall back to the unknown row
    assert _table_conviction(t, 0.9, None) == t["unknown"][2]
    assert _table_conviction(t, 0.9, "weird") == t["unknown"][2]
    assert _table_conviction(t, None, "confirms") == t["confirms"][0]
    # matrix semantics: contradicts/ignores never clear the gate on their own
    thr = LongTermAgentConfig.LONGTERM_CONFIDENCE_THRESHOLD
    assert max(t["contradicts"]) < thr and max(t["ignores"]) < thr


# --- llm mode: back-compat -------------------------------------------------------
def test_llm_gate_backcompat_and_new_fields(db, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "CONVICTION_GATE", "llm")
    out = risk_gate_node(_state(0.8), db, cfg)
    sig = out["signal"]
    # no penalties fired for TESTX → gating value == raw LLM conviction, trade passes
    assert sig["conviction"] == 0.8
    assert sig["conviction_raw"] == 0.8
    assert sig["conviction_gate"] == sig["conviction"]
    assert sig["gate"] == "llm"
    assert out["filtered_signal"] is not None


# --- table mode ------------------------------------------------------------------
def test_table_gate_demotes_confident_contrarian(db, cfg, monkeypatch):
    # the INTC/QCOM failure shape: LLM is 0.9-confident on a contradicts-cell thesis;
    # the table gates it at contradicts[strong]=0.45 → FILTERED, raw preserved.
    monkeypatch.setattr(cfg, "CONVICTION_GATE", "table")
    out = risk_gate_node(_state(0.9, verdict="contradicts", shift_score=0.9), db, cfg)
    sig = out["signal"]
    assert sig["gate"] == "table"
    assert sig["conviction_raw"] == 0.9
    assert sig["conviction"] == cfg.CONVICTION_TABLE["contradicts"][2]
    assert out["filtered_signal"] is None


def test_table_gate_confirms_strong_passes(db, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "CONVICTION_GATE", "table")
    out = risk_gate_node(_state(0.3, verdict="confirms", shift_score=0.8), db, cfg)
    # weak LLM self-report does not matter; evidence-based base clears the gate
    assert out["signal"]["conviction"] == cfg.CONVICTION_TABLE["confirms"][2]
    assert out["filtered_signal"] is not None


def test_table_gate_applies_advisory_nudge(db, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "CONVICTION_GATE", "table")
    out = risk_gate_node(_state(0.3, verdict="confirms", shift_score=0.5, nudge=-0.1), db, cfg)
    assert out["signal"]["conviction"] == round(cfg.CONVICTION_TABLE["confirms"][1] - 0.1, 2)


def test_table_gate_no_shift_cap_still_wins(db, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "CONVICTION_GATE", "table")
    st = _state(0.9, verdict="confirms", shift_score=0.9)
    st["background_shift"] = {"has_shifted": False}
    out = risk_gate_node(st, db, cfg)
    assert out["signal"]["conviction"] < cfg.LONGTERM_CONFIDENCE_THRESHOLD
    assert out["filtered_signal"] is None


# --- counter-trend guard -----------------------------------------------------------
def test_counter_trend_short_into_rally_filtered(db, cfg):
    # the INTC case: SHORT after a +317% trailing move → penalty knocks it under the gate
    out = risk_gate_node(_state(0.7, direction="SHORT", tech_ret=317.0), db, cfg)
    assert any("counter-trend" in f for f in out["risk_flags"])
    assert out["signal"]["conviction"] == round(0.7 - cfg.COUNTER_TREND_PENALTY, 2)
    assert out["filtered_signal"] is None


def test_counter_trend_with_trend_untouched(db, cfg):
    # LONG in the same +317% rally is WITH the trend — guard silent
    out = risk_gate_node(_state(0.8, direction="LONG", tech_ret=317.0), db, cfg)
    assert not any("counter-trend" in f for f in out["risk_flags"])
    # LONG into a collapse IS counter-trend
    out2 = risk_gate_node(_state(0.8, direction="LONG", tech_ret=-60.0), db, cfg)
    assert any("counter-trend" in f for f in out2["risk_flags"])


def test_counter_trend_below_threshold_or_missing_tech_silent(db, cfg):
    out = risk_gate_node(_state(0.8, direction="SHORT", tech_ret=30.0), db, cfg)
    assert not any("counter-trend" in f for f in out["risk_flags"])
    out2 = risk_gate_node(_state(0.8, direction="SHORT"), db, cfg)  # no technicals in state
    assert not any("counter-trend" in f for f in out2["risk_flags"])


def test_counter_trend_disabled_by_zero_penalty(db, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "COUNTER_TREND_PENALTY", 0.0)
    out = risk_gate_node(_state(0.7, direction="SHORT", tech_ret=317.0), db, cfg)
    assert not any("counter-trend" in f for f in out["risk_flags"])
    assert out["signal"]["conviction"] == 0.7


# --- provenance --------------------------------------------------------------------
def test_provenance_carries_gate_knobs():
    knobs = LongTermAgentConfig.provenance()["knobs"]
    assert knobs["conviction_gate"] in ("llm", "table")
    assert knobs["counter_trend_pct"] == LongTermAgentConfig.COUNTER_TREND_PCT
    assert knobs["counter_trend_penalty"] == LongTermAgentConfig.COUNTER_TREND_PENALTY
    assert set(knobs["conviction_table"]) == {"confirms", "contradicts", "ignores", "unknown"}
