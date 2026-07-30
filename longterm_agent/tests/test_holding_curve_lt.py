"""Guards for the three places the midday holding-curve harness can silently lie.

1. **The entry point.** `entry_grid_point` is the whole PIT claim of a midday cohort — if
   it rounds the wrong way the trade fills at a bar that opened before the agent finished.
2. **The x axis.** Two legs (minute bars, then daily closes) share one axis; a one-point
   error in `held_session_minutes` shifts every multi-day exit and nothing downstream ever
   looks wrong.
3. **The block statistics.** The CI must count cohort Mondays, not trades — on a book with
   29 of 41 trades on two Mondays the difference is the whole verdict.

Pure logic only: no DB, no LLM, no network.
"""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from longterm_agent.holding_curve_lt import (
    _cohort_bootstrap,
    _sign_flip_p,
    paired_shape,
    parse_ref,
    summarise,
    variance_split,
)
from longterm_agent.intraday_entry import (
    GRID_POINTS,
    SESSION_BARS,
    SESSION_OPEN,
    chained_return,
    decision_moment,
    entry_grid_point,
    eod_price,
    first_real_bar,
    held_session_minutes,
    is_session_day,
    next_session,
    parse_grid,
)

MONDAY = date(2026, 6, 1)


def _point_to_clock(pt: int) -> time:
    return (datetime.combine(MONDAY, SESSION_OPEN) + timedelta(minutes=pt)).time()


class TestEntryPoint:
    """A midday decision at 12:00 + 1 minute of agent time fills at 12:02, not 12:01."""

    def test_noon_plus_one_minute_fills_at_the_next_bar(self):
        d = decision_moment(MONDAY, time(12, 0), 1)
        assert d == datetime(2026, 6, 1, 12, 1)
        assert _point_to_clock(entry_grid_point(d)) == time(12, 2)

    def test_at_or_after_is_exactly_one_minute_looser(self):
        d = decision_moment(MONDAY, time(12, 0), 1)
        strict = entry_grid_point(d, at_or_after=False)
        loose = entry_grid_point(d, at_or_after=True)
        assert _point_to_clock(loose) == time(12, 1)
        assert strict - loose == 1

    def test_mid_minute_decision_agrees_under_both_conventions(self):
        # 12:01:30 — the 12:01 bar already opened, so both conventions must say 12:02.
        d = datetime(2026, 6, 1, 12, 1, 30)
        assert entry_grid_point(d, at_or_after=False) == entry_grid_point(d, at_or_after=True)
        assert _point_to_clock(entry_grid_point(d)) == time(12, 2)

    @pytest.mark.parametrize("minute", range(0, 390, 17))
    def test_fill_bar_never_opens_before_the_decision(self, minute):
        """The property the whole experiment rests on, checked across the session."""
        d = decision_moment(MONDAY, SESSION_OPEN, minute + 1)
        pt = entry_grid_point(d)
        fill = datetime.combine(MONDAY, SESSION_OPEN) + timedelta(minutes=pt)
        assert fill > d

    def test_decision_after_the_last_bar_is_untradable(self):
        # 15:59 is the final bar (point 389); a decision at 15:59:30 has no bar left.
        assert entry_grid_point(datetime(2026, 6, 1, 15, 59, 30)) >= SESSION_BARS

    def test_fractional_agent_time_is_honoured(self):
        d = decision_moment(MONDAY, time(12, 0), 0.5)
        assert d == datetime(2026, 6, 1, 12, 0, 30)
        assert _point_to_clock(entry_grid_point(d)) == time(12, 1)


class TestFirstRealBar:
    def test_searches_forward_only(self):
        real = np.zeros(SESSION_BARS, dtype=bool)
        real[100] = True   # a print BEFORE the wanted minute must never be used
        real[153] = True
        assert first_real_bar(real, 152, tolerance=5) == 153

    def test_gives_up_past_tolerance(self):
        real = np.zeros(SESSION_BARS, dtype=bool)
        real[160] = True
        assert first_real_bar(real, 152, tolerance=5) is None

    def test_no_bar_after_the_close(self):
        assert first_real_bar(np.ones(SESSION_BARS, dtype=bool), SESSION_BARS, 5) is None


class TestHoldingAxis:
    """Both legs measure the same thing: minutes the position is exposed to the market."""

    def test_intraday_leg_is_its_own_minutes(self):
        assert held_session_minutes(152, ("intraday", 30)) == 30

    def test_entry_day_close_is_the_seam_between_the_legs(self):
        entry = 152  # 12:02
        to_close = SESSION_BARS - entry
        assert held_session_minutes(entry, ("intraday", to_close)) == to_close
        assert held_session_minutes(entry, ("eod", 0)) == to_close

    def test_each_further_session_adds_one_full_day(self):
        entry = 152
        a = held_session_minutes(entry, ("eod", 3))
        b = held_session_minutes(entry, ("eod", 4))
        assert b - a == SESSION_BARS

    def test_overnight_gap_contributes_nothing(self):
        """Wall clock from Monday 12:02 to Tuesday's close is >27h; the tradable span is
        238 + 390 minutes. Using wall clock would put the two legs on different axes."""
        assert held_session_minutes(152, ("eod", 1)) == 238 + 390

    def test_a_later_entry_holds_less_of_the_entry_day(self):
        assert (held_session_minutes(380, ("eod", 0))
                < held_session_minutes(20, ("eod", 0)))

    def test_unknown_leg_is_an_error_not_a_guess(self):
        with pytest.raises(ValueError):
            held_session_minutes(152, ("weekly", 2))


class TestGridParsing:
    def test_builds_both_legs(self):
        grid = parse_grid("5,1,5", "0,2")
        assert grid == [("intraday", 1), ("intraday", 5), ("eod", 0), ("eod", 2)]

    def test_rejects_a_zero_minute_hold(self):
        with pytest.raises(ValueError):
            parse_grid("0,5", "1")

    def test_rejects_negative_trading_days(self):
        with pytest.raises(ValueError):
            parse_grid("5", "-1")


class TestExitHelpers:
    def test_eod_price_returns_none_past_the_series(self):
        closes = [(date(2026, 6, 1), 10.0), (date(2026, 6, 2), 11.0)]
        assert eod_price(closes, 0) == (date(2026, 6, 1), 10.0)
        assert eod_price(closes, 5) is None

    def test_chained_return_compounds(self):
        assert chained_return([0.1, 0.1]) == pytest.approx(0.21)
        assert chained_return([]) == 0.0

    def test_a_holiday_cohort_has_no_session(self):
        # 2026-05-25 (Memorial Day) is on the Monday grid and has zero minute bars.
        assert is_session_day(None) is False
        assert is_session_day(("prices", "real")) is True


class TestHolidayRoll:
    """The rule: a cohort on a closed market trades the NEXT session, it is not lost."""

    # Memorial Day week 2026: Mon 05-25 closed, Tue 05-26 open.
    WEEK = {date(2026, 5, 26), date(2026, 5, 27), date(2026, 5, 28), date(2026, 5, 29)}

    def test_memorial_day_monday_rolls_to_tuesday(self):
        assert next_session(date(2026, 5, 25), self.WEEK) == date(2026, 5, 26)

    def test_an_open_monday_does_not_move(self):
        assert next_session(date(2026, 5, 26), self.WEEK) == date(2026, 5, 26)

    def test_it_rolls_over_consecutive_closures(self):
        sessions = {date(2026, 5, 28)}
        assert next_session(date(2026, 5, 25), sessions) == date(2026, 5, 28)

    def test_it_refuses_to_roll_into_another_news_week(self):
        # A gap longer than the max is missing data, not a holiday; rolling a full week
        # would move the cohort's decision into a different news background.
        assert next_session(date(2026, 5, 25), {date(2026, 6, 8)}) is None


def _panel(per_cohort: dict[date, list[float]]) -> pd.DataFrame:
    rows = []
    for cohort, rets in per_cohort.items():
        for r in rets:
            rows.append({"leg": "eod", "spec": 20, "cohort": cohort, "held_min": 7800.0,
                         "ret_pct": r, "censored": False, "always_long_pct": 0.0,
                         "excess_spy_from_close": 0.0})
    return pd.DataFrame(rows)


class TestBlockStatistics:
    def test_ci_counts_cohorts_not_trades(self):
        """20 trades that are really 2 bets must not get a 20-bet confidence interval."""
        clumped = _panel({date(2026, 4, 27): [3.0] * 10, date(2026, 5, 4): [-3.0] * 10})
        spread = _panel({date(2026, 4, 27) + timedelta(days=7 * i): [3.0 if i % 2 else -3.0]
                         for i in range(20)})
        rng = np.random.default_rng(0)
        c_lo, c_hi = _cohort_bootstrap(clumped, "ret_pct", 500, rng)
        s_lo, s_hi = _cohort_bootstrap(spread, "ret_pct", 500, rng)
        assert (c_hi - c_lo) > (s_hi - s_lo)

    def test_ci_is_undefined_on_a_single_cohort(self):
        one = _panel({date(2026, 4, 27): [1.0, 2.0, 3.0]})
        lo, hi = _cohort_bootstrap(one, "ret_pct", 200, np.random.default_rng(0))
        assert np.isnan(lo) and np.isnan(hi)

    def test_sign_flip_cannot_reject_a_symmetric_book(self):
        book = _panel({date(2026, 4, 27) + timedelta(days=7 * i): [1.0 if i % 2 else -1.0]
                       for i in range(8)})
        assert _sign_flip_p(book, 500, np.random.default_rng(0)) > 0.05

    def test_paired_test_is_narrower_than_the_level_test(self):
        """The point of pairing: a common cohort shock cancels inside each trade.

        Both horizons here move together by a large per-Monday amount, and differ by a
        steady +0.5pp. The level CI has to swallow the shock; the paired CI does not.
        """
        rows = []
        for c in range(6):
            shock = 8.0 * (1 if c % 2 else -1)  # the Monday everyone rode
            cohort = date(2026, 4, 27) + timedelta(days=7 * c)
            for t in range(4):
                tid = c * 4 + t
                for leg, spec, held, extra in (("eod", 20, 8038.0, 0.0),
                                               ("eod", 30, 12000.0, 0.5)):
                    rows.append({"trade_id": tid, "leg": leg, "spec": spec, "cohort": cohort,
                                 "held_min": held, "ret_pct": shock + extra, "censored": False,
                                 "always_long_pct": 0.0, "excess_spy_from_close": 0.0})
        panel = pd.DataFrame(rows)

        lvl = summarise(panel, 500, 200, seed=3)
        lvl_w = float((lvl["ci_hi"] - lvl["ci_lo"]).max())
        pair = paired_shape(panel, ("eod", 20), 500, seed=3)
        row = pair[pair["spec"] == 30].iloc[0]

        assert row["mean_diff_pct"] == pytest.approx(0.5)
        assert row["beats_ref_rate"] == 1.0
        assert (row["ci_hi"] - row["ci_lo"]) < lvl_w / 10
        assert row["ci_lo"] > 0  # the shape is detected where the level test cannot be

    def test_paired_reference_row_is_identically_zero(self):
        panel = _panel({date(2026, 4, 27) + timedelta(days=7 * i): [1.0, 2.0] for i in range(4)})
        panel["trade_id"] = range(len(panel))
        out = paired_shape(panel, ("eod", 20), 200, seed=0)
        assert out.iloc[0]["mean_diff_pct"] == 0.0

    def test_paired_only_compares_trades_present_at_both_horizons(self):
        rows = []
        for tid in range(6):
            cohort = date(2026, 4, 27) + timedelta(days=7 * (tid % 3))
            rows.append({"trade_id": tid, "leg": "eod", "spec": 20, "cohort": cohort,
                         "held_min": 8038.0, "ret_pct": 1.0, "censored": False,
                         "always_long_pct": 0.0, "excess_spy_from_close": 0.0})
            if tid < 4:  # the long horizon truncates the last two trades away
                rows.append({"trade_id": tid, "leg": "eod", "spec": 60, "cohort": cohort,
                             "held_min": 23638.0, "ret_pct": 3.0, "censored": False,
                             "always_long_pct": 0.0, "excess_spy_from_close": 0.0})
        out = paired_shape(pd.DataFrame(rows), ("eod", 20), 200, seed=0)
        assert int(out[out["spec"] == 60].iloc[0]["n_paired"]) == 4
        assert out[out["spec"] == 60].iloc[0]["mean_diff_pct"] == pytest.approx(2.0)

    def test_variance_split_separates_the_two_sources(self):
        between = _panel({date(2026, 4, 27) + timedelta(days=7 * i): [5.0 * i] * 4
                          for i in range(4)})
        within = _panel({date(2026, 4, 27) + timedelta(days=7 * i): [-7.0, -2.0, 2.0, 7.0]
                         for i in range(4)})
        b_between, b_within = variance_split(between, "eod", 20)
        w_between, w_within = variance_split(within, "eod", 20)
        assert b_between > b_within
        assert w_within > w_between

    def test_reference_parsing_rejects_nonsense(self):
        assert parse_ref("eod:20") == ("eod", 20)
        assert parse_ref("intraday:30") == ("intraday", 30)
        for bad in ("weekly:2", "eod", "eod:"):
            with pytest.raises(ValueError):
                parse_ref(bad)

    def test_summary_is_ordered_by_time_held_across_both_legs(self):
        rows = []
        for i, (leg, spec, held) in enumerate(
                [("eod", 1, 628.0), ("intraday", 30, 30.0), ("eod", 0, 238.0)]):
            for c in range(3):
                rows.append({"leg": leg, "spec": spec,
                             "cohort": date(2026, 4, 27) + timedelta(days=7 * c),
                             "held_min": held, "ret_pct": 0.1 * i, "censored": False,
                             "always_long_pct": 0.0, "excess_spy_from_close": 0.0})
        out = summarise(pd.DataFrame(rows), 200, 200, seed=1)
        assert list(out["held_min"]) == [30.0, 238.0, 628.0]
        assert list(out["label"]) == ["30m", "0d", "1d"]
        assert (out["n_cohorts"] == 3).all()
