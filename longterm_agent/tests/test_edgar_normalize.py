"""
Pure-function tests for the EDGAR XBRL → statement normalization (no network/DB).

Locks in the behaviors we hand-verified:
  - duration classification: single quarter (Q) and full year (FY) kept; YTD spans skipped
  - point-in-time: filed_date = the ORIGINAL filing (earliest), not later restatements
  - balance-sheet comparative dedup: one record per balance date, keyed to its original filing
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bloomberg_news_fetcher"))
from edgar_fetcher import extract_statements


def _facts():
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"start": "2025-01-01", "end": "2025-03-31", "val": 100, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-15"},
            {"start": "2024-01-01", "end": "2024-12-31", "val": 380, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-10"},
            # 6-month YTD span — must be skipped (not a single quarter or full year)
            {"start": "2025-01-01", "end": "2025-06-30", "val": 210, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-07-15"},
            # restated Q1 in a later amendment — must NOT override the original value
            {"start": "2025-01-01", "end": "2025-03-31", "val": 999, "fy": 2025, "fp": "Q1", "form": "10-Q/A", "filed": "2025-09-01"},
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2025-01-01", "end": "2025-03-31", "val": 20, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-15"},
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2025-03-31", "val": 1000, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-15"},
            # same balance date repeated as a comparative in the later Q2 10-Q
            {"end": "2025-03-31", "val": 1000, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-07-15"},
        ]}},
    }}}


def _by(recs, kind, period_end):
    return [r for r in recs if r["kind"] == kind and str(r["period_end"]) == period_end]


def test_quarterly_income_extracted():
    recs = extract_statements(_facts())
    q1 = _by(recs, "income_statement", "2025-03-31")
    assert len(q1) == 1
    assert q1[0]["data"]["revenue"] == 100
    assert q1[0]["data"]["net_income"] == 20
    assert q1[0]["fiscal_period"] == "Q1"


def test_annual_income_extracted():
    recs = extract_statements(_facts())
    fy = _by(recs, "income_statement", "2024-12-31")
    assert len(fy) == 1 and fy[0]["data"]["revenue"] == 380 and fy[0]["fiscal_period"] == "FY"


def test_ytd_span_skipped():
    # the 6-month YTD revenue (210) must never appear as a period value
    recs = extract_statements(_facts())
    revenues = [r["data"].get("revenue") for r in recs if r["kind"] == "income_statement"]
    assert 210 not in revenues


def test_filed_date_is_original_not_restatement():
    # original Q1 filing is 2025-04-15; the 999 restatement (2025-09-01) must be ignored
    recs = extract_statements(_facts())
    q1 = _by(recs, "income_statement", "2025-03-31")[0]
    assert q1["filed_date"] == date(2025, 4, 15)
    assert q1["data"]["revenue"] == 100  # not 999


def test_balance_sheet_comparative_deduped():
    # the same balance date appears in two filings; keep ONE record at the original filing
    recs = extract_statements(_facts())
    bs = _by(recs, "balance_sheet", "2025-03-31")
    assert len(bs) == 1
    assert bs[0]["filed_date"] == date(2025, 4, 15)  # original, not the 07-15 comparative
    assert bs[0]["fiscal_period"] == "Q1"


def test_empty_facts():
    assert extract_statements({"facts": {"us-gaap": {}}}) == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
