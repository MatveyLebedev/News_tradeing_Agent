"""`in_session` must answer "was the market open", not "is the clock in range"."""
from datetime import datetime

import pytest

from longterm_agent import exit_rules as er


# --- weekday --------------------------------------------------------------------------
def test_a_weekday_midday_is_a_session():
    assert er.in_session(datetime(2026, 8, 10, 12, 40))        # Monday


def test_sunday_midday_is_not_a_session():
    """The 2026-07-26 failure: a Sunday batch overran into 09:30-16:00 and 59 rows were
    treated as in-session decisions that can never fill."""
    assert not er.in_session(datetime(2026, 7, 26, 12, 40))    # Sunday


def test_saturday_midday_is_not_a_session():
    assert not er.in_session(datetime(2026, 8, 8, 12, 40))     # Saturday


def test_the_sunday_batch_hours_stay_out_of_session():
    """06:01 was already correct — the fix must not change what already worked."""
    assert not er.in_session(datetime(2026, 8, 9, 6, 1))


# --- clock, unchanged --------------------------------------------------------------------
@pytest.mark.parametrize("t,expected", [((9, 29), False), ((9, 30), True),
                                        ((15, 59), True), ((16, 0), False)])
def test_the_session_window_is_unchanged(t, expected):
    assert er.in_session(datetime(2026, 8, 10, *t)) is expected


# --- holiday, via the price table ---------------------------------------------------------
class _Cur:
    def __init__(self, traded):
        self.traded, self._r = traded, None

    def execute(self, sql, params=None):
        self._r = (1,) if self.traded else None

    def fetchone(self):
        return self._r


def test_market_traded_on_reads_the_benchmark():
    assert er.market_traded_on(_Cur(True), "2026-08-10")
    assert not er.market_traded_on(_Cur(False), "2026-08-10")


def test_an_unreadable_table_assumes_a_session():
    """Unknown must keep the stricter PENDING path, never invent a fill."""
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("no such table")
    assert er.market_traded_on(_Broken(), "2026-08-10")


# --- resolve_entry: holiday vs thin ticker -------------------------------------------------
class _ResolveCur:
    """No minute print; `traded` decides whether the DAY had a session."""

    def __init__(self, traded_that_day):
        self.traded = traded_that_day
        self._mode = None

    def execute(self, sql, params=None):
        self._mode = "spy" if "stock_prices_daily" in sql and "SPY" in sql else "other"
        if self._mode == "other" and "historical_data" in sql:
            self._mode = "minute"

    def fetchone(self):
        if self._mode == "spy":
            return (1,) if self.traded else None
        return None            # no minute bar, no daily entry bar


def test_a_holiday_falls_through_to_the_daily_convention(monkeypatch):
    """No session that day ⇒ there was no in-session decision to price, so the daily rule
    is the right answer rather than a fallback."""
    seen = {}

    def fake_entry_bar(cur, tk, d, tol=7, **k):
        seen["called"] = True
        return (datetime(2026, 9, 8).date(), 210.0)     # the next session's open

    monkeypatch.setattr(er, "entry_bar", fake_entry_bar)
    out = er.resolve_entry(_ResolveCur(traded_that_day=False), "AAPL",
                           datetime(2026, 9, 7).date(), datetime(2026, 9, 7, 12, 0))
    assert seen.get("called"), "a holiday must reach the daily convention"
    assert out[0] == er.FILL_CONVENTION      # open_t1, not mid_1m


def test_a_thin_ticker_on_a_real_session_stays_pending(monkeypatch):
    """The market traded and this name did not print — a data hole, not a convention
    change. It must NOT silently become open_t1."""
    monkeypatch.setattr(er, "entry_bar",
                        lambda *a, **k: pytest.fail("changed convention on a live session"))
    out = er.resolve_entry(_ResolveCur(traded_that_day=True), "AAPL",
                           datetime(2026, 8, 10).date(), datetime(2026, 8, 10, 12, 0))
    assert out is None
