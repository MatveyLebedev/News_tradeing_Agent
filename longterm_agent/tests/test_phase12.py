"""Phase 12 tests: STORED-field wiring into the fundamentals verdict (pure — no DB)."""
from datetime import date

from longterm_agent.tools.fundamentals_tool import (
    _cash_flow_block, _balance_block, _opex_block, _eps_trajectory, _street_block)
from longterm_agent.schemas import FundamentalsTrend


def _q(period, **data):
    return {"period_end": date.fromisoformat(period), "fiscal_period": data.pop("fp", "Q1"),
            "data": data}


# --- cash flow ---------------------------------------------------------------
def test_cash_flow_block_ttm():
    qs = [_q("2026-03-31", operating_cash_flow=100.0, capex=20.0, stock_buyback=30.0, dividends_paid=10.0),
          _q("2025-12-31", operating_cash_flow=90.0, capex=25.0, stock_buyback=30.0, dividends_paid=10.0),
          _q("2025-09-30", operating_cash_flow=80.0, capex=15.0, stock_buyback=0, dividends_paid=10.0),
          _q("2025-06-30", operating_cash_flow=70.0, capex=20.0, stock_buyback=0, dividends_paid=10.0)]
    b = _cash_flow_block(qs)
    assert b["quarters"] == 4
    assert b["fcf"] == 340.0 - 80.0
    assert b["capex_intensity"] == round(80.0 / 340.0, 3)
    assert b["capital_return_vs_fcf"] == round(100.0 / 260.0, 3)


def test_cash_flow_block_handles_missing():
    assert _cash_flow_block([]) == {"quarters": 0}
    qs = [_q("2026-03-31", operating_cash_flow=None, capex=5.0)]
    assert _cash_flow_block(qs)["quarters"] == 0


# --- balance -----------------------------------------------------------------
def test_balance_block():
    b = _balance_block({"long_term_debt": 100.0, "short_term_debt": 20.0,
                        "current_assets": 300.0, "current_liabilities": 150.0,
                        "cash_and_equivalents": 50.0, "stockholders_equity": 240.0})
    assert b["current_ratio"] == 2.0
    assert b["net_debt"] == 70.0
    assert b["debt_to_equity"] == 0.5
    assert _balance_block(None) == {}


# --- opex --------------------------------------------------------------------
def test_opex_intensity_and_yoy_delta():
    income = [
        _q("2026-03-31", fp="Q1", revenue=200.0, rd_expense=40.0, sga_expense=20.0),
        _q("2025-12-31", fp="Q4", revenue=190.0, rd_expense=30.0, sga_expense=19.0),
        _q("2025-03-31", fp="Q1", revenue=100.0, rd_expense=10.0, sga_expense=10.0),
    ]
    o = _opex_block(income)
    assert o["rd_intensity"] == 0.2
    assert o["rd_intensity_yoy_delta"] == round(0.2 - 0.1, 3)  # fiscal-matched vs Q1 last year
    assert o["sga_intensity"] == 0.1


# --- eps trajectory ------------------------------------------------------------
def test_eps_trajectory_slope():
    income = [_q("2026-03-31", eps_basic=3.0), _q("2025-12-31", eps_basic=2.5),
              _q("2025-09-30", eps_basic=2.0), _q("2025-06-30", eps_basic=1.5)]
    t = _eps_trajectory(income)
    assert t["series_newest_first"] == [3.0, 2.5, 2.0, 1.5]
    assert t["slope"] == "rising" and t["avg_qoq_delta"] == 0.5
    falling = _eps_trajectory(list(reversed(income)))
    assert falling["slope"] == "falling"
    assert "slope" not in _eps_trajectory(income[:2])  # <3 points → no slope claim


# --- street --------------------------------------------------------------------
def test_street_block():
    info = {"targetMeanPrice": 120.0, "recommendationMean": 1.8, "recommendationKey": "buy",
            "beta": 1.3, "fiftyTwoWeekHigh": 150.0, "fiftyTwoWeekLow": 50.0}
    s = _street_block(info, price=100.0)
    assert s["target_gap"] == 0.2
    assert s["pos_52w"] == 0.5
    assert s["beta"] == 1.3
    assert _street_block({}, 100.0) == {}
    assert "target_gap" not in _street_block(info, None)  # no PIT price → no gap claim


# --- schema --------------------------------------------------------------------
def test_trend_schema_has_sub_verdicts_with_safe_defaults():
    ft = FundamentalsTrend(verdict="confirms", reasoning="x")
    d = ft.model_dump()
    for k in ("growth_sub", "profitability_sub", "balance_sheet_sub",
              "capital_allocation_sub", "street_sub"):
        assert d[k] == "unknown"  # absent evidence must not fabricate a sub-verdict
