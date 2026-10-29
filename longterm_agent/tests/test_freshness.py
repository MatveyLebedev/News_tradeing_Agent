"""Pure-logic tests for the Phase 2 data-freshness gate (no DB).

evaluate_freshness is the pure half of core.freshness; the DB half (check_freshness)
is a thin query wrapper. Also covers the graders' pending_data guard.
"""
from datetime import date, datetime

from core.freshness import evaluate_freshness, _weekdays_between
from longterm_agent.grade_shifts import _has_data_through

NOW = datetime(2026, 7, 10, 12, 0)  # Friday
TH = {"news": 2, "prices": 5, "prices_spy": 5, "fundamentals": 14}


def _maxes(**overrides):
    base = {
        "news": datetime(2026, 7, 9, 16, 0),
        "prices": date(2026, 7, 9),
        "prices_spy": date(2026, 7, 9),
        "fundamentals": date(2026, 7, 4),
    }
    base.update(overrides)
    return base


def test_weekdays_between():
    assert _weekdays_between(date(2026, 7, 6), date(2026, 7, 10)) == 4   # Mon → Fri
    assert _weekdays_between(date(2026, 7, 3), date(2026, 7, 6)) == 1    # Fri → Mon
    assert _weekdays_between(date(2026, 7, 10), date(2026, 7, 12)) == 0  # Fri → Sun (weekend only)
    assert _weekdays_between(date(2026, 7, 10), date(2026, 7, 10)) == 0


def test_all_fresh():
    rep = evaluate_freshness(_maxes(), now=NOW, thresholds=TH)
    assert rep["any_stale"] is False
    assert rep["stale"] == []
    assert rep["sources"]["prices_spy"]["age"] == 1  # Thu → Fri


def test_frozen_spy_is_flagged():
    # Replay of the 2026-06-15 incident: universe fresh, benchmark frozen.
    rep = evaluate_freshness(_maxes(prices_spy=date(2026, 6, 15)), now=NOW, thresholds=TH)
    assert rep["any_stale"] is True
    assert rep["stale"] == ["prices_spy"]


def test_stale_news_calendar_days():
    rep = evaluate_freshness(_maxes(news=datetime(2026, 7, 7, 9, 0)), now=NOW, thresholds=TH)
    assert "news" in rep["stale"]  # 3 calendar days > 2


def test_error_and_missing_are_fail_closed():
    rep = evaluate_freshness(
        _maxes(news=RuntimeError("boom"), fundamentals=None), now=NOW, thresholds=TH)
    assert set(rep["stale"]) == {"news", "fundamentals"}
    assert "boom" in rep["sources"]["news"]["error"]


def test_threshold_override_tightens_prices():
    # age 4 weekdays: fine at the ad-hoc default (5), stale at the Sunday-preflight 2.
    m = _maxes(prices=date(2026, 7, 6), prices_spy=date(2026, 7, 6))  # Mon → Fri = 4
    assert evaluate_freshness(m, now=NOW, thresholds=TH)["any_stale"] is False
    tight = {**TH, "prices": 2, "prices_spy": 2}
    rep = evaluate_freshness(m, now=NOW, thresholds=tight)
    assert set(rep["stale"]) == {"prices", "prices_spy"}


# --- graders' pending_data guard ---------------------------------------------

class _FakeCur:
    """A gap-free daily series ending at `max_date`, answering the guard's exact-date
    lookup. (Holes are exercised in test_exit_bar_guard.py.)"""

    def __init__(self, max_date):
        self.max_date = max_date
        self.executed = 0
        self._day = None

    def execute(self, sql, params):
        self.executed += 1
        self._day = params[1]

    def fetchone(self):
        return (100.0,) if self.max_date is not None and self._day <= self.max_date else None


def test_has_data_through():
    assert _has_data_through(_FakeCur(date(2026, 7, 9)), "SPY", date(2026, 7, 9), {}) is True
    # frozen series: no exit bar for the target → NOT gradable (pending_data)
    assert _has_data_through(_FakeCur(date(2026, 6, 15)), "SPY", date(2026, 7, 1), {}) is False
    # no rows at all → not gradable
    assert _has_data_through(_FakeCur(None), "XXXX", date(2026, 7, 1), {}) is False


def test_has_data_through_caches_per_exit_session():
    cur = _FakeCur(date(2026, 7, 9))
    cache = {}
    # Fri 07-03 (Independence Day observed), Sat 07-04, Sun 07-05 all exit at Thu 07-02
    for target in (date(2026, 7, 3), date(2026, 7, 4), date(2026, 7, 5)):
        assert _has_data_through(cur, "SPY", target, cache) is True
    assert cur.executed == 1  # one exit session, one lookup
