"""Matrix-cell eligibility gate (`LT_REQUIRE_MATRIX_CELL`).

The rule is conformance, not a bet: a LONG/SHORT is only licensed by the (shift sign,
verdict) cell the agent's own synthesis prompt defines. These tests pin the table to the
prompt text and pin the gate to the table, so a change to `SYNTHESIS_MATRIX_RULES` that is
not mirrored in `MATRIX_CELLS` fails here rather than silently in a book six weeks later.

Needs the DB (priced_in_check / earnings_event_risk read it); skips if unreachable.
"""
from datetime import datetime

import pytest

from longterm_agent.agent.nodes import (MATRIX_CELLS, SYNTHESIS_MATRIX_RULES,
                                        matrix_licenses, risk_gate_node)

GOOD_FUND = {"has_statements": True, "revenue_yoy": 0.5,
             "margin_series": [{"gross_margin": 0.6, "operating_margin": 0.4, "net_margin": 0.3}]}


def _state(direction, verdict, shift_dir, conviction=0.80, has_shifted=True):
    fund = dict(GOOD_FUND)
    fund["trend"] = {"verdict": verdict} if verdict is not None else {}
    return {"ticker": "TESTX", "as_of": datetime(2026, 6, 15), "article_count": 300,
            "signal": {"direction": direction, "conviction": conviction},
            "fundamentals": fund,
            "background_shift": {"has_shifted": has_shifted, "direction": shift_dir,
                                 "shift_score": 0.8}}


def _vetoed(out):
    return "no_matrix_cell" in out["gate_trace"]["vetoes"]


# --- the table itself -----------------------------------------------------------------
def test_table_matches_the_prompt_text():
    """Every licensed cell must be a line in the rules the model is actually given."""
    assert "shift POSITIVE + fundamentals improving → high-conviction LONG" in SYNTHESIS_MATRIX_RULES
    assert "shift NEGATIVE + fundamentals deteriorating → high-conviction SHORT" in SYNTHESIS_MATRIX_RULES
    assert "overreaction: contrarian LONG or NEUTRAL" in SYNTHESIS_MATRIX_RULES
    assert "hype risk: low conviction / avoid" in SYNTHESIS_MATRIX_RULES
    assert set(MATRIX_CELLS) == {("positive", "confirms"), ("negative", "confirms"),
                                 ("negative", "contradicts")}


@pytest.mark.parametrize("shift_dir,verdict,side", [
    ("positive", "confirms", "LONG"),
    ("negative", "confirms", "SHORT"),
    ("negative", "contradicts", "LONG"),      # the overreaction cell the matrix names
])
def test_licensed_cells(shift_dir, verdict, side):
    assert matrix_licenses(shift_dir, verdict, side)


@pytest.mark.parametrize("shift_dir,verdict,side", [
    ("positive", "contradicts", "SHORT"),     # hype risk → avoid, not short
    ("positive", "contradicts", "LONG"),      # hype risk → avoid, not buy either
    ("negative", "contradicts", "SHORT"),     # overreaction licenses LONG/NEUTRAL only
    ("positive", "confirms", "SHORT"),        # right cell, wrong side
    ("negative", "confirms", "LONG"),         # right cell, wrong side
    ("negative", "ignores", "SHORT"),
    ("positive", "ignores", "LONG"),
    ("negative", "unknown", "LONG"),
    ("negative", None, "LONG"),
    (None, "confirms", "SHORT"),              # shift fired but carries no direction
])
def test_unlicensed_cells(shift_dir, verdict, side):
    assert not matrix_licenses(shift_dir, verdict, side)


# --- the gate -------------------------------------------------------------------------
@pytest.mark.parametrize("shift_dir,verdict,side", [
    ("positive", "confirms", "LONG"),
    ("negative", "confirms", "SHORT"),
    ("negative", "contradicts", "LONG"),
])
def test_gate_lets_licensed_cells_through(db, cfg, shift_dir, verdict, side):
    out = risk_gate_node(_state(side, verdict, shift_dir), db, cfg)
    assert not _vetoed(out)
    assert out["signal"]["conviction"] >= cfg.LONGTERM_CONFIDENCE_THRESHOLD


@pytest.mark.parametrize("shift_dir,verdict,side", [
    ("positive", "contradicts", "SHORT"),
    ("negative", "contradicts", "SHORT"),
    ("negative", "ignores", "SHORT"),
    ("positive", "ignores", "LONG"),
    ("negative", "unknown", "LONG"),
    (None, "confirms", "SHORT"),
])
def test_gate_vetoes_unlicensed_cells(db, cfg, shift_dir, verdict, side):
    out = risk_gate_node(_state(side, verdict, shift_dir), db, cfg)
    assert _vetoed(out)
    assert out["signal"]["conviction"] < cfg.LONGTERM_CONFIDENCE_THRESHOLD
    assert out["filtered_signal"] is None
    assert any("no matrix cell" in f for f in out["risk_flags"])


def test_neutral_is_never_touched(db, cfg):
    """The veto is about which SIDE a cell licenses; an abstention has no side."""
    out = risk_gate_node(_state("NEUTRAL", "ignores", "negative"), db, cfg)
    assert not _vetoed(out)


def test_shift_direction_is_stamped(db, cfg):
    """Unrecoverable after the fact — the corpus that produced it is not reproducible."""
    out = risk_gate_node(_state("LONG", "confirms", "positive"), db, cfg)
    assert out["gate_trace"]["table_inputs"]["shift_direction"] == "positive"


def test_flag_off_is_an_exact_no_op(db, cfg, monkeypatch):
    st = _state("SHORT", "ignores", "negative")
    monkeypatch.setattr(cfg, "REQUIRE_MATRIX_CELL", False)
    off = risk_gate_node(st, db, cfg)
    monkeypatch.setattr(cfg, "REQUIRE_MATRIX_CELL", True)
    on = risk_gate_node(_state("SHORT", "ignores", "negative"), db, cfg)
    assert "no_matrix_cell" not in off["gate_trace"]["vetoes"]
    assert "no_matrix_cell" in on["gate_trace"]["vetoes"]
    # the stamp is unconditional: a replay needs it in both arms
    assert off["gate_trace"]["table_inputs"]["shift_direction"] == "negative"
