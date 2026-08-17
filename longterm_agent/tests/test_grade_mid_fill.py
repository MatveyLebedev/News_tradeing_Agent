"""`mid_1m` in the backtest grader: chosen per row from the cohort's own clock.

The rule these encode: a cohort stamped IN SESSION fills on minute bars, anything else
keeps the daily convention it already had. The second half matters as much as the first —
a change to the grader that silently re-prices books already in the table would rewrite
history, so "no stamp" and "stamped pre-open" must stay byte-identical to before.

Pure: `_decision_moment` is arithmetic, and the convention choice is exercised through
`exit_rules.resolve_entry` with a fake cursor. No DB, no network.
"""
from datetime import date, datetime

import pytest

from longterm_agent import exit_rules as er
from longterm_agent.backtest import _decision_moment

MONDAY = date(2026, 8, 10)


# --- the clock ------------------------------------------------------------------------
def test_missing_stamp_means_no_decision_moment():
    """Books written before `cohort_time_et` existed must take the daily path."""
    assert _decision_moment(MONDAY, None) is None
    assert _decision_moment(MONDAY, "") is None


def test_stamp_is_combined_with_the_cohort_date():
    assert _decision_moment(MONDAY, "12:00") == datetime(2026, 8, 10, 12, 0)
    assert _decision_moment(MONDAY, "06:30") == datetime(2026, 8, 10, 6, 30)


def test_seconds_in_the_stamp_are_tolerated():
    assert _decision_moment(MONDAY, "12:00:30") == datetime(2026, 8, 10, 12, 0)


@pytest.mark.parametrize("bad", ["noon", "12", ":", "12:xx", None, 1200])
def test_an_unparseable_stamp_falls_back_rather_than_guessing(bad):
    assert _decision_moment(MONDAY, bad) is None


# --- which clocks are in session ------------------------------------------------------
def test_pre_open_and_weekend_stamps_are_not_in_session():
    """06:30 Monday and 06:00 Sunday are the two live books; neither fills intraday."""
    assert not er.in_session(datetime(2026, 8, 10, 6, 30))
    assert not er.in_session(datetime(2026, 8, 9, 6, 0))
    assert er.in_session(datetime(2026, 8, 10, 12, 0))


# --- the convention choice ------------------------------------------------------------
DAILY_BAR = (date(2026, 8, 11), 101.0, 101.5)   # the open_t1 fallback bar


class _Cur:
    """Minimal cursor covering the three queries `resolve_entry` can reach:
    the per-ticker minute table, the SPY session probe, and the daily entry bar."""

    def __init__(self, minute_row=None, traded=True):
        self.minute_row, self.traded = minute_row, traded
        self._one, self._all = None, []

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if "historical_data_" in s:                       # entry_bar_mid
            self._one, self._all = self.minute_row, []
        elif "ticker='SPY'" in s and "LIMIT 1" in s:      # market_traded_on
            self._one, self._all = ((1,) if self.traded else None), []
        else:                                             # entry_bar (daily)
            self._one, self._all = None, [DAILY_BAR]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def test_midday_decision_resolves_to_the_minute_fill():
    bar = (datetime(2026, 8, 10, 12, 2), 100.0, 100.5)
    conv, d, px = er.resolve_entry(_Cur(bar), "AAPL", MONDAY,
                                   datetime(2026, 8, 10, 12, 1))
    assert conv == er.FILL_CONVENTION_MID
    assert (d, px) == (date(2026, 8, 10), 100.0)


def test_a_hole_on_a_trading_day_stays_pending_instead_of_falling_back():
    """No print + market open = data hole. Silently using the daily open would move the
    fill hours before the decision, which is the leak this convention exists to avoid."""
    assert er.resolve_entry(_Cur(None, traded=True), "AAPL", MONDAY,
                            datetime(2026, 8, 10, 12, 1)) is None


def test_a_market_holiday_takes_the_daily_convention_not_pending():
    """No session at all: there was no in-session decision to price."""
    out = er.resolve_entry(_Cur(None, traded=False), "AAPL", MONDAY,
                           datetime(2026, 8, 10, 12, 1))
    assert out is not None and out[0] == er.FILL_CONVENTION


def test_pre_open_decision_keeps_the_daily_convention():
    bar = (datetime(2026, 8, 10, 12, 2), 100.0, 100.5)
    out = er.resolve_entry(_Cur(bar), "AAPL", MONDAY, datetime(2026, 8, 10, 6, 30))
    assert out[0] == er.FILL_CONVENTION      # the minute bar is NOT used


# --- injection: the arithmetic after the entry is convention-blind ---------------------
def test_injected_entry_prices_the_hold_book_from_the_minute_fill():
    class _C:
        def execute(self, sql, args=None):
            self.r = (120.0,)                 # close_on_or_before

        def fetchone(self):
            return self.r

    out = er.t1_return(_C(), "AAPL", MONDAY, 28, entry=(date(2026, 8, 10), 100.0))
    assert out["entry_px"] == 100.0
    assert out["ret"] == pytest.approx(0.20)
