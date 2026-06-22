"""
Point-in-time valuation reconstruction (Backtest Plan B2 — the cheap fundamentals fix).

trailing PE / P/B / P/S are reconstructed from EDGAR (TTM, as-filed) × the daily close, so
valuation is available at ANY as_of with no forward-only yfinance asymmetry. These are
integration tests: they use the `db` fixture and skip cleanly when data is missing.
"""
from datetime import datetime, date, timedelta

import psycopg2
import pytest

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.tools.fundamentals_tool import (
    valuation_pit, read_fundamentals, _close_on_or_before, _ttm_sum,
)

TICKER = "NVDA"
AS_OF = datetime(2026, 5, 1)


def _q(sql, args):
    conn = psycopg2.connect(**LongTermAgentConfig.get_db_config())
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.close()
    return rows


def test_pit_pe_present_and_sane(db):
    v = valuation_pit(db, TICKER, AS_OF)
    if "trailingPE" not in v:
        pytest.skip("no TTM net income / price at as_of")
    assert v["source"] == "edgar+price"
    assert v["trailingPE"] > 0
    assert 1 < v["trailingPE"] < 500, f"implausible reconstructed PE {v['trailingPE']}"
    # the metrics should be self-consistent: PE ≈ price / ttm_eps
    assert abs(v["trailingPE"] - v["price"] / v["ttm_eps"]) < 0.05


def test_pit_uses_asof_price_not_future(db):
    """The price input must be the close on/before as_of — never a later bar (no lookahead)."""
    v = valuation_pit(db, TICKER, AS_OF)
    if v.get("price") is None:
        pytest.skip("no price near as_of")
    close, cdate = _close_on_or_before(db, TICKER, AS_OF.date())
    assert v["price"] == close
    assert date.fromisoformat(v["price_date"]) <= AS_OF.date()


def test_pit_is_deterministic(db):
    assert valuation_pit(db, TICKER, AS_OF) == valuation_pit(db, TICKER, AS_OF)


def test_ttm_reconstruction_matches_manual(db):
    """TTM net income via FY + stub − prior should equal a hand sum of the same periods.
    Anchored at a date where only the FY (no new stub quarter) is filed → TTM == latest FY."""
    # as_of just after the FY2026 10-K filing but before Q1'27 was filed → TTM should equal FY2026
    rows = _q("""SELECT period_end, filed_date, (data->>'net_income')::float
                 FROM fundamentals WHERE ticker=%s AND source='edgar'
                 AND kind='income_statement' AND fiscal_period='FY'
                 ORDER BY period_end DESC LIMIT 1""", [TICKER])
    if not rows:
        pytest.skip("no FY income row")
    fy_end, fy_filed, fy_ni = rows[0]
    as_of = datetime.combine(fy_filed + timedelta(days=1), datetime.min.time())
    # ensure no later quarter is filed by then
    later = _q("""SELECT COUNT(*) FROM fundamentals WHERE ticker=%s AND source='edgar'
                  AND kind='income_statement' AND fiscal_period<>'FY'
                  AND period_end > %s AND filed_date <= %s""", [TICKER, fy_end, as_of.date()])
    if later[0][0] != 0:
        pytest.skip("a stub quarter is already filed at the FY+1d anchor")
    from longterm_agent.tools.fundamentals_tool import _edgar_income_all
    inc = _edgar_income_all(db, TICKER, as_of)
    ttm = _ttm_sum(inc, lambda d: d.get("net_income"))
    assert ttm is not None and abs(ttm - fy_ni) < 1.0


def test_pit_fixes_asymmetry_before_first_snapshot(db):
    """The point of B2's cheap fix: valuation exists BEFORE the first yfinance snapshot, where
    the old forward-only path returned an empty valuation."""
    rows = _q("SELECT MIN(fetch_date) FROM fundamentals WHERE source='yfinance' AND ticker=%s",
              [TICKER])
    min_fetch = rows[0][0] if rows else None
    if not min_fetch:
        pytest.skip("no yfinance snapshot")
    before = datetime.combine(min_fetch - timedelta(days=1), datetime.min.time())
    v = valuation_pit(db, TICKER, before)
    if "trailingPE" not in v:
        pytest.skip("no EDGAR TTM / price before the first yfinance snapshot")
    f = read_fundamentals(db, TICKER, before)
    assert f["valuation"].get("trailingPE") is not None
    assert f["valuation_pit"]["source"] == "edgar+price"
