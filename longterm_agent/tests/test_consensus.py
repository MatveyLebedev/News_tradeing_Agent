"""Consensus groundwork tests (2026-07-15): pure format_consensus — PIT filtering,
surprise math, graceful degradation. No DB, no network."""
from datetime import date

from longterm_agent.tools.fundamentals_tool import format_consensus

EST = {
    "earnings_estimate": [
        {"period": "0q", "avg": 1.89, "low": 1.83, "high": 1.99, "numberOfAnalysts": 31},
        {"period": "+1q", "avg": 2.01, "low": 1.87, "high": 2.15, "numberOfAnalysts": 29},
        {"period": "0y", "avg": 8.76},
    ],
    "revenue_estimate": [
        {"period": "0q", "avg": 108_900_000_000, "low": 107_000_000_000, "high": 112_000_000_000},
    ],
    "eps_trend": [
        {"period": "0q", "current": 1.894, "30daysAgo": 1.894, "90daysAgo": 1.729},
        {"period": "+1q", "current": 2.01},
    ],
}
FMP = {"records": [
    # upcoming (actuals null)
    {"date": "2026-07-30", "epsActual": None, "epsEstimated": 1.88,
     "revenueActual": None, "revenueEstimated": 108_847_000_000},
    {"date": "2026-10-29", "epsActual": None, "epsEstimated": 2.02,
     "revenueActual": None, "revenueEstimated": 114_000_000_000},
    # past reports
    {"date": "2026-04-30", "epsActual": 2.01, "epsEstimated": 1.94,
     "revenueActual": 102_466_000_000, "revenueEstimated": 100_000_000_000},
    {"date": "2026-01-29", "epsActual": 2.84, "epsEstimated": 2.67,
     "revenueActual": 139_000_000_000, "revenueEstimated": 137_000_000_000},
]}


def test_full_view():
    out = format_consensus(EST, FMP, date(2026, 7, 15))
    assert out["available"] is True
    assert out["street_forward"]["eps_0q"]["avg"] == 1.89
    assert out["street_forward"]["revenue_0q"]["avg"] == 108_900_000_000
    assert "eps_0y" not in out["street_forward"]           # only 0q/+1q kept (compact)
    assert out["eps_trend_0q"]["90daysAgo"] == 1.729
    assert out["next_report"]["date"] == "2026-07-30"       # earliest AFTER as_of
    assert out["next_report"]["eps_estimated"] == 1.88
    s = out["recent_surprises"]
    assert s[0]["date"] == "2026-04-30"                     # newest past first
    assert s[0]["eps_surprise_pct"] == 3.6                  # (2.01-1.94)/1.94
    assert s[0]["revenue_surprise_pct"] == 2.5              # (102.5B-100B)/100B


def test_pit_filtering_moves_boundary():
    # at as_of=2026-02-01 the April report is UPCOMING and January is the last past one…
    early = format_consensus(None, FMP, date(2026, 2, 1))
    assert early["next_report"]["date"] == "2026-04-30"
    assert early["recent_surprises"][0]["date"] == "2026-01-29"
    # …and a future-dated row never appears among past surprises
    assert all(r["date"] <= "2026-02-01" for r in early["recent_surprises"])


def test_missing_everything_degrades():
    out = format_consensus(None, None, date(2026, 7, 15))
    assert out["available"] is False and "note" in out
    out2 = format_consensus({}, {"records": []}, date(2026, 7, 15))
    assert out2["available"] is False


def test_null_estimates_do_not_crash_surprise_math():
    fmp = {"records": [{"date": "2026-04-30", "epsActual": 2.0, "epsEstimated": None,
                        "revenueActual": None, "revenueEstimated": 1_000}]}
    out = format_consensus(None, fmp, date(2026, 7, 15))
    s = out["recent_surprises"][0]
    assert s["eps_surprise_pct"] is None and s["revenue_surprise_pct"] is None
