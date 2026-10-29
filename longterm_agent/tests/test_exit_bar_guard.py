"""A missing exit bar must hold a grade back, never price it at the session before.

The incident (2026-10-29): the 2026-09-22 daily bar of 95 tickers first landed on 09-25
20:30 ET, after 09-23 and 09-24 had. The graders' guard asked only whether the series'
MAX date reached the exit target, so on 09-24/25 it passed, `close_on_or_before(09-22)`
fell through the hole to the 09-21 close, and the 2026-09-07 cohort's 2w was graded with
it (GS fwd −7.80% stored vs −8.75% real). A filled grade is never revisited, so it stayed.

The fix names the exit session from the NYSE calendar and requires that session's own bar,
for the stock and the benchmark. The calendar is what keeps a weekend or holiday target
gradable: it resolves to the session before, by rule, not by whatever bar happens to be
there. Pure — a fake cursor over in-memory series, no DB.
"""
from datetime import date

import pytest

from longterm_agent import exit_rules as er
from longterm_agent.market_calendar import (is_session, last_session_on_or_before,
                                            nyse_holidays, sessions_between)

COHORT = date(2026, 9, 7)        # Labor Day Monday: the mid_1m fill rolled to open_t1
ENTRY = date(2026, 9, 8)         # first session after as_of
EXIT_2W = date(2026, 9, 22)      # ENTRY + 14 — the bar that landed late
HOLE = {EXIT_2W}


def _series(start, end, hole=frozenset(), base=100.0, step=1.0):
    """{date: (open, close)} for every session in [start, end] except `hole`; the close
    rises by `step` per session so any off-by-one-session exit shows in the return."""
    out, px = {}, base
    for d in sessions_between(start, end):
        if d not in hole:
            out[d] = (px, px)
        px += step
    return out


class _Cur:
    """Answers the four daily-bar queries the exit helpers issue, over {ticker: series}."""

    def __init__(self, series):
        self.series = {t.upper(): s for t, s in series.items()}
        self.queries = 0
        self._rows = []

    def execute(self, sql, args=None):
        self.queries += 1
        s = " ".join(sql.split())
        bars = sorted(self.series.get(args[0], {}).items())
        if "AND date=%s" in s:                                   # close_on
            self._rows = [(c,) for d, (o, c) in bars if d == args[1]]
        elif "ORDER BY date DESC LIMIT 1" in s:                  # close_on_or_before
            hits = [(c,) for d, (o, c) in bars if args[2] <= d <= args[1]]
            self._rows = hits[-1:]
        elif "SELECT date, open, close" in s:                    # entry_bar
            lo_ok = (lambda d: d >= args[1]) if "date >= %s" in s else (lambda d: d > args[1])
            self._rows = [(d, o, c) for d, (o, c) in bars
                          if lo_ok(d) and d <= args[2]][:args[3]]
        elif "SELECT date, close" in s:                          # fetch_price_path
            lo_ok = (lambda d: d >= args[1]) if "date >= %s" in s else (lambda d: d > args[1])
            self._rows = [(d, c) for d, (o, c) in bars if lo_ok(d) and d <= args[2]]
        else:
            raise AssertionError(f"unexpected query: {s}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _cur(stock_hole=frozenset(), spy_hole=frozenset(), end=date(2026, 10, 9), **kw):
    return _Cur({"GS": _series(date(2026, 8, 24), end, stock_hole, **kw),
                 "SPY": _series(date(2026, 8, 24), end, spy_hole, base=500.0)})


# --- the calendar ---------------------------------------------------------------------
@pytest.mark.parametrize("day", [
    date(2026, 9, 7),    # Labor Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 7, 3),    # Independence Day, Saturday → observed Friday
    date(2026, 11, 26),  # Thanksgiving
    date(2027, 6, 18),   # Juneteenth, Saturday → observed Friday
    date(2027, 12, 24),  # Christmas, Saturday → observed Friday
    date(2025, 1, 9),    # unscheduled: the Carter day of mourning
])
def test_closed_days(day):
    assert not is_session(day)


def test_new_years_day_on_a_saturday_closes_nothing():
    """NYSE Rule 7.2's one exception: Friday Dec 31 is the year-end accounting day."""
    assert date(2021, 12, 31) not in nyse_holidays(2021)
    assert date(2021, 12, 31) not in nyse_holidays(2022)
    assert is_session(date(2021, 12, 31))


def test_juneteenth_only_from_2022():
    assert is_session(date(2021, 6, 18)) and not is_session(date(2022, 6, 20))


def test_weekend_and_holiday_targets_resolve_to_the_session_before():
    assert last_session_on_or_before(date(2026, 9, 26)) == date(2026, 9, 25)   # Sat
    assert last_session_on_or_before(date(2026, 9, 7)) == date(2026, 9, 4)     # Labor Day
    assert last_session_on_or_before(date(2026, 7, 5)) == date(2026, 7, 2)     # Sun after the observed 4th
    assert last_session_on_or_before(EXIT_2W) == EXIT_2W                       # a session is itself


# --- the guard ------------------------------------------------------------------------
def test_guard_refuses_a_missing_exit_bar_even_when_later_bars_exist():
    """The incident state: 09-23 and 09-24 present, 09-22 not."""
    cur = _cur(stock_hole=HOLE, end=date(2026, 9, 24))
    assert er.has_exit_bar(cur, "GS", EXIT_2W) is False
    assert er.has_exit_bar(cur, "SPY", EXIT_2W) is True


def test_guard_checks_the_benchmark_leg_on_its_own():
    cur = _cur(spy_hole=HOLE, end=date(2026, 9, 24))
    assert er.has_exit_bar(cur, "GS", EXIT_2W) is True
    assert er.has_exit_bar(cur, "SPY", EXIT_2W) is False


def test_guard_passes_a_weekend_target_on_the_fridays_bar():
    cur = _cur(end=date(2026, 9, 25))                       # nothing after Friday yet
    assert er.has_exit_bar(cur, "GS", date(2026, 9, 26)) is True
    assert er.has_exit_bar(cur, "GS", date(2026, 9, 27)) is True


def test_guard_refuses_a_session_that_has_not_ended_before_the_grade():
    """A fetch run mid-session writes that day's bar at an intraday price (07-21 15:47 ET);
    a grade on the exit day itself must not read it."""
    cur = _cur(end=EXIT_2W)                                  # the 09-22 bar is in
    assert er.has_exit_bar(cur, "GS", EXIT_2W, today=EXIT_2W) is False
    assert er.has_exit_bar(cur, "GS", EXIT_2W, today=date(2026, 9, 23)) is True
    # a Saturday target graded on that Saturday: Friday's session is over
    assert er.has_exit_bar(_cur(end=date(2026, 9, 25)), "GS", date(2026, 9, 26),
                           today=date(2026, 9, 26)) is True


def test_guard_refuses_a_frozen_series():
    """The 2026-06-15 SPY freeze, which the old max-date rule was written for."""
    cur = _cur(end=date(2026, 9, 18))
    assert er.has_exit_bar(cur, "SPY", EXIT_2W) is False


def test_grade_shifts_alias_is_the_new_guard():
    from longterm_agent.grade_shifts import _has_data_through
    assert _has_data_through is er.has_exit_bar


# --- the hold book --------------------------------------------------------------------
def test_hold_return_is_none_across_the_hole_and_exact_once_it_fills():
    held = er.t1_return(_cur(stock_hole=HOLE), "GS", COHORT, 14)
    assert held is None                     # was: priced at the 09-21 close
    filled = er.t1_return(_cur(), "GS", COHORT, 14)
    entry_px = _series(date(2026, 8, 24), ENTRY)[ENTRY][0]
    exit_px = _series(date(2026, 8, 24), EXIT_2W)[EXIT_2W][1]
    assert filled["entry_date"] == ENTRY
    assert filled["ret"] == pytest.approx((exit_px - entry_px) / entry_px)


def test_hold_return_resolves_weekend_and_holiday_targets():
    cur, s = _cur(), _series(date(2026, 8, 24), date(2026, 10, 9))
    # Sun as_of 08-23 → entry Mon 08-24; +14 = Labor Day 09-07 → exit at Fri 09-04's close
    r = er.t1_return(cur, "GS", date(2026, 8, 23), 14)
    e = s[date(2026, 8, 24)][0]
    assert r["ret"] == pytest.approx((s[date(2026, 9, 4)][1] - e) / e)
    # ENTRY + 18 = Sat 09-26 → exit at Fri 09-25's close
    r = er.t1_return(cur, "GS", COHORT, 18)
    e = s[ENTRY][0]
    assert r["ret"] == pytest.approx((s[date(2026, 9, 25)][1] - e) / e)


# --- the stopped book -----------------------------------------------------------------
def test_stopped_book_refuses_a_hole_at_the_horizon_exit():
    assert er.stopped_returns(_cur(stock_hole=HOLE), "GS", COHORT, "LONG", 14,
                              stop_loss=0.5) is None


def test_stopped_book_refuses_a_hole_inside_the_walk():
    """A hole before the exit can be the day the stop fired."""
    assert er.stopped_returns(_cur(stock_hole={date(2026, 9, 15)}), "GS", COHORT,
                              "LONG", 14, stop_loss=0.5) is None


def test_stopped_book_refuses_a_benchmark_hole_on_the_exit_day():
    assert er.stopped_returns(_cur(spy_hole=HOLE), "GS", COHORT, "LONG", 14,
                              stop_loss=0.5) is None


def test_stopped_book_ignores_a_hole_after_a_stop():
    """Bars after the stop day cannot change the trade."""
    # falling series: the LONG from the 09-08 open breaches −10% before the 09-25 hole
    cur = _cur(stock_hole={date(2026, 9, 25)}, step=-1.0)
    out = er.stopped_returns(cur, "GS", COHORT, "LONG", 28, stop_loss=0.10)
    assert out is not None and out["reason"] == "stop"
    assert out["exit_date"] < date(2026, 9, 25)


def test_stopped_book_on_a_complete_series_exits_at_the_horizon_session():
    out = er.stopped_returns(_cur(), "GS", COHORT, "LONG", 14, stop_loss=0.5)
    assert out["reason"] == "horizon" and out["exit_date"] == EXIT_2W


# --- the path check helper ------------------------------------------------------------
def test_missing_sessions_skips_weekends_and_holidays():
    path = [(d, 1.0) for d in sessions_between(date(2026, 9, 1), date(2026, 9, 30))]
    assert er.missing_sessions(path, date(2026, 9, 1), date(2026, 9, 30)) == []
    holed = [p for p in path if p[0] != EXIT_2W]
    assert er.missing_sessions(holed, date(2026, 9, 1), date(2026, 9, 30)) == [EXIT_2W]
    assert date(2026, 9, 7) not in {d for d, _ in path}       # Labor Day isn't expected


def test_every_series_bar_is_a_session():
    """The fake series is built from the calendar; sanity-check the span it covers."""
    s = _series(date(2026, 8, 24), date(2026, 10, 9))
    assert all(is_session(d) for d in s)
    assert len(s) == len(sessions_between(date(2026, 8, 24), date(2026, 10, 9)))
    assert date(2026, 9, 7) not in s and ENTRY in s
