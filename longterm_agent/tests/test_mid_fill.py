"""`mid_1m` in the LIVE execution path (exit_rules.resolve_entry / entry_bar_mid).

The rule under test is the one docs/STRATEGY_MONDAY_MIDDAY.md freezes: an in-session
decision fills at the OPEN of the first minute bar starting STRICTLY AFTER the decision.
The research harness implements the same rule in longterm_agent/intraday_entry.py; the
last test pins the two together so they cannot drift apart.
"""
from datetime import date, datetime, time

import pytest

from longterm_agent.exit_rules import (DEFAULT_MINUTE_TOLERANCE, FILL_CONVENTION,
                                       FILL_CONVENTION_MID, entry_bar_mid, in_session,
                                       minute_table, resolve_entry)


class FakeCursor:
    """Minute bars as (timestamp, open, close), plus a daily fallback for resolve_entry."""

    def __init__(self, minute_rows=None, daily_rows=None, table_missing=False):
        self.minute_rows = sorted(minute_rows or [])
        self.daily_rows = daily_rows or []
        self.table_missing = table_missing
        self._result = []

    def execute(self, sql, params):
        if "historical_data_" in sql:
            if self.table_missing:
                raise RuntimeError('relation "historical_data_x" does not exist')
            lo, hi = params
            self._result = [r for r in self.minute_rows if lo < r[0] <= hi][:1]
        else:
            self._result = self.daily_rows[:params[-1]] if self.daily_rows else []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def bars(day, start="09:30", n=390, px=100.0):
    h, m = (int(x) for x in start.split(":"))
    base = datetime.combine(day, time(h, m))
    from datetime import timedelta
    return [(base + timedelta(minutes=i), px + i * 0.01, px + i * 0.01) for i in range(n)]


DAY = date(2026, 4, 27)


class TestInSession:
    def test_midday_is_in_session(self):
        assert in_session(datetime.combine(DAY, time(12, 0)))

    def test_open_bell_is_in_session(self):
        assert in_session(datetime.combine(DAY, time(9, 30)))

    def test_premarket_is_not(self):
        assert not in_session(datetime.combine(DAY, time(6, 30)))

    def test_close_is_not(self):
        # 16:00 is the closing print, not a moment you can still buy at.
        assert not in_session(datetime.combine(DAY, time(16, 0)))

    def test_after_hours_is_not(self):
        assert not in_session(datetime.combine(DAY, time(18, 0)))


class TestEntryBarMid:
    def test_fills_at_the_next_minute_strictly_after(self):
        cur = FakeCursor(bars(DAY))
        got = entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(12, 0)))
        assert got is not None
        d, px = got
        assert d == DAY
        # 09:30 is bar 0, so 12:01 is bar 151 — the first one starting after 12:00.
        assert px == pytest.approx(100.0 + 151 * 0.01)

    def test_same_minute_never_fills_the_decision(self):
        """A bar stamped 12:02 covers 12:02:00-12:02:59; filling a 12:02:00 decision there
        would buy at a price printed in the minute the decision was made."""
        cur = FakeCursor(bars(DAY))
        _, px = entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(12, 2)))
        assert px == pytest.approx(100.0 + 153 * 0.01)  # 12:03, not 12:02

    def test_latency_pushes_the_fill_later(self):
        cur = FakeCursor(bars(DAY))
        _, px = entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(12, 0)),
                              latency_minutes=5)
        assert px == pytest.approx(100.0 + 156 * 0.01)  # 12:06

    def test_gap_inside_tolerance_still_fills(self):
        every_ten = [b for b in bars(DAY) if b[0].minute % 10 == 0]
        cur = FakeCursor(every_ten)
        got = entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(12, 1)))
        assert got is not None and got[0] == DAY

    def test_hole_beyond_tolerance_is_none(self):
        thin = [b for b in bars(DAY) if b[0].hour < 12]
        cur = FakeCursor(thin)
        assert entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(12, 30))) is None

    def test_out_of_session_is_none(self):
        cur = FakeCursor(bars(DAY))
        assert entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(6, 30))) is None
        assert entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(16, 30))) is None

    def test_decision_in_the_last_minute_has_no_fill(self):
        cur = FakeCursor(bars(DAY))
        assert entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(15, 59))) is None

    def test_tolerance_never_runs_past_the_close(self):
        cur = FakeCursor(bars(DAY) + bars(DAY, start="16:05", n=5, px=999.0))
        got = entry_bar_mid(cur, "AAPL", datetime.combine(DAY, time(15, 55)))
        assert got is None or got[1] < 500.0  # never the after-hours print

    def test_missing_table_is_none_not_a_crash(self):
        cur = FakeCursor(table_missing=True)
        assert entry_bar_mid(cur, "SPY", datetime.combine(DAY, time(12, 0))) is None


class TestResolveEntry:
    def test_in_session_decision_picks_mid(self):
        cur = FakeCursor(bars(DAY))
        conv, d, _ = resolve_entry(cur, "AAPL", DAY, datetime.combine(DAY, time(12, 0)))
        assert conv == FILL_CONVENTION_MID and d == DAY

    def test_no_decision_time_keeps_the_daily_convention(self):
        cur = FakeCursor(daily_rows=[(date(2026, 4, 28), 101.0, 101.5)])
        conv, d, px = resolve_entry(cur, "AAPL", DAY, None)
        assert conv == FILL_CONVENTION and d == date(2026, 4, 28) and px == 101.0

    def test_preopen_decision_keeps_the_daily_convention(self):
        """A 06:30 Sunday/Monday book must be untouched by this change."""
        cur = FakeCursor(bars(DAY), daily_rows=[(date(2026, 4, 28), 101.0, 101.5)])
        conv, _, _ = resolve_entry(cur, "AAPL", DAY, datetime.combine(DAY, time(6, 30)))
        assert conv == FILL_CONVENTION

    def test_decision_on_another_day_keeps_the_daily_convention(self):
        """run_timestamp drifting off the as_of day (a late re-persist) must not be read
        as an intraday fill on a day the decision did not belong to."""
        cur = FakeCursor(bars(DAY), daily_rows=[(date(2026, 4, 28), 101.0, 101.5)])
        conv, _, _ = resolve_entry(cur, "AAPL", DAY,
                                   datetime.combine(date(2026, 4, 29), time(12, 0)))
        assert conv == FILL_CONVENTION

    def test_in_session_without_minute_bars_is_pending_not_a_fallback(self):
        """Silently falling back to open_t1 would grade a midday trade at the NEXT
        session's open and call it the same book."""
        cur = FakeCursor(minute_rows=[], daily_rows=[(date(2026, 4, 28), 101.0, 101.5)])
        assert resolve_entry(cur, "AAPL", DAY, datetime.combine(DAY, time(12, 0))) is None


class TestAgreesWithResearchHarness:
    def test_same_fill_minute_as_intraday_entry(self):
        """The live path and longterm_agent/intraday_entry.py must agree on WHICH minute
        a decision fills at, or a forward book and its backtest measure different things."""
        from longterm_agent.intraday_entry import decision_moment, entry_grid_point

        for hh, mm, agent_min in ((12, 0, 1.0), (12, 2, 1.0), (9, 45, 0.0), (14, 30, 2.0)):
            grid_pt = entry_grid_point(decision_moment(DAY, time(hh, mm), agent_min))
            cur = FakeCursor(bars(DAY))
            _, px = entry_bar_mid(cur, "AAPL",
                                  datetime.combine(DAY, time(hh, mm)),
                                  latency_minutes=int(agent_min))
            assert px == pytest.approx(100.0 + grid_pt * 0.01), f"{hh}:{mm}+{agent_min}"


def test_minute_table_name_matches_the_writer():
    assert minute_table("BRK-B") == "historical_data_brk_b"
    assert minute_table("aapl") == "historical_data_aapl"


def test_tolerance_default_is_sane():
    assert 1 <= DEFAULT_MINUTE_TOLERANCE <= 60
