"""open_t1 fill convention tests: entry_bar / t1_return / stopped_returns on a stub
cursor (no DB), worst_position_return with the explicit entry, and the PIT daily-bar
cutoff for decision-side price reads."""
from datetime import date, datetime, timedelta

from longterm_agent.exit_rules import entry_bar, stopped_returns, t1_return
from longterm_agent.targets import worst_position_return
from longterm_agent.tools.pit import daily_bar_cutoff

FRI = date(2026, 7, 17)
SUN = date(2026, 7, 19)
MON = date(2026, 7, 20)
TUE = date(2026, 7, 21)


class StubCur:
    """Serves the three stock_prices_daily query shapes from an in-memory bar list.
    bars: {ticker: [(date, open, close), ...] ascending}."""

    def __init__(self, bars):
        self.bars = bars
        self._res = []

    def execute(self, sql, params):
        rows = self.bars.get(params[0], [])
        if "ORDER BY date DESC" in sql:            # close_on_or_before
            hi, lo = params[1], params[2]
            sel = [r for r in rows if lo <= r[0] <= hi]
            self._res = [(sel[-1][2],)] if sel else []
            return
        # entry_bar / fetch_price_path: honor the lower-bound operator (> vs >= for d0)
        after = (lambda d, lo: d >= lo) if "date >= %s" in sql else (lambda d, lo: d > lo)
        lo, hi = params[1], params[2]
        if "LIMIT" in sql:                         # entry_bar
            self._res = [r for r in rows if after(r[0], lo) and r[0] <= hi][:params[3]]
        else:                                      # fetch_price_path
            self._res = [(d, c) for d, o, c in rows if after(d, lo) and d <= hi]

    def fetchall(self):
        return list(self._res)

    def fetchone(self):
        return self._res[0] if self._res else None


def _bars(*triples):
    return [(d, float(o), float(c)) for d, o, c in triples]


def test_weekend_asof_enters_at_monday_open():
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (MON, 105, 103))})
    assert entry_bar(cur, "NKE", SUN) == (MON, 105.0)


def test_friday_asof_enters_at_monday_open_too():
    # backtest cohorts are Friday-midnight stamps; the Friday bar is a decision INPUT,
    # so the first tradable bar is Monday
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (MON, 105, 103))})
    assert entry_bar(cur, "NKE", FRI) == (MON, 105.0)


def test_holiday_monday_falls_through_to_tuesday():
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (TUE, 99, 98))})
    assert entry_bar(cur, "NKE", SUN) == (TUE, 99.0)


def test_nth_2_gives_the_t2_diagnostic_book():
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (MON, 105, 103), (TUE, 99, 98))})
    assert entry_bar(cur, "NKE", SUN, nth=2) == (TUE, 99.0)


def test_no_bar_within_tolerance_returns_none():
    cur = StubCur({"NKE": _bars((FRI, 100, 101))})
    assert entry_bar(cur, "NKE", SUN) is None
    assert entry_bar(cur, "NKE", SUN, nth=2) is None


def test_open_d0_preopen_cohort_enters_same_day():
    # Monday 06:30 cohort (PIT cutoff active): Monday's own 09:30 open is the first fill
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (MON, 105, 103), (TUE, 99, 98))})
    assert entry_bar(cur, "NKE", MON, include_asof_day=True) == (MON, 105.0)
    # nth=2 under d0 = the T+2 diagnostic book (Tuesday open)
    assert entry_bar(cur, "NKE", MON, nth=2, include_asof_day=True) == (TUE, 99.0)
    # holiday Monday: no Monday bar → falls through to Tuesday
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (TUE, 99, 98))})
    assert entry_bar(cur, "NKE", MON, include_asof_day=True) == (TUE, 99.0)


def test_open_d0_stopped_includes_entry_day_close():
    # d0 entry Mon open 100; Monday's own close −12% must breach the stop on day 0
    bars = {"NKE": _bars((FRI, 100, 101), (MON, 100, 88), (TUE, 96, 95)),
            "SPY": _bars((FRI, 500, 501), (MON, 500, 500), (TUE, 500, 502))}
    s = stopped_returns(StubCur(bars), "NKE", MON, "LONG", 28, benchmark="SPY",
                        stop_loss=0.10, include_asof_day=True)
    assert s["reason"] == "stop" and s["days"] == 0 and s["entry_date"] == MON
    assert abs(s["fwd"] - (88 - 100) / 100) < 1e-9


def test_t1_return_excludes_the_weekend_gap():
    # Fri close 101 → Mon open 105 gap is NOT in the book: return measures 105 → exit
    exit_day = MON + timedelta(days=28)
    cur = StubCur({"NKE": _bars((FRI, 100, 101), (MON, 105, 103), (exit_day, 111, 110))})
    r = t1_return(cur, "NKE", SUN, 28)
    assert r["entry_date"] == MON and r["entry_px"] == 105.0
    assert abs(r["ret"] - (110 - 105) / 105) < 1e-9


def test_t1_return_none_before_entry_bar_lands():
    cur = StubCur({"NKE": _bars((FRI, 100, 101))})
    assert t1_return(cur, "NKE", SUN, 28) is None


def test_stopped_returns_uses_open_entry_and_day_counting():
    bars = {"NKE": _bars((FRI, 100, 101), (MON, 100, 97), (TUE, 96, 89)),
            "SPY": _bars((FRI, 500, 501), (MON, 500, 500), (TUE, 500, 502))}
    cur = StubCur(bars)
    s = stopped_returns(cur, "NKE", SUN, "LONG", 28, benchmark="SPY", stop_loss=0.10)
    assert s["reason"] == "stop" and s["entry_date"] == MON and s["entry_px"] == 100.0
    assert abs(s["fwd"] - (89 - 100) / 100) < 1e-9
    assert s["days"] == 1  # Mon entry, Tue breach close
    assert abs(s["bench"] - (502 - 500) / 500) < 1e-9


def test_worst_position_return_counts_entry_day_close():
    closes = [(MON, 95.0), (TUE, 102.0)]
    assert worst_position_return(closes, "LONG", 100.0) == -0.05
    assert worst_position_return(closes, "SHORT", 100.0) == -0.02
    assert worst_position_return(closes, "NEUTRAL", 100.0) is None
    assert worst_position_return([], "LONG", 100.0) is None


def test_daily_bar_cutoff_session_completion():
    # backtest Friday-midnight stamp: last completed session is Thursday
    assert daily_bar_cutoff(datetime(2026, 7, 17, 0, 0)) == date(2026, 7, 16)
    # live Sunday 06:00: Saturday cutoff → effectively Friday's bar (last existing)
    assert daily_bar_cutoff(datetime(2026, 7, 19, 6, 0)) == date(2026, 7, 18)
    # Monday pre-open vs post-close
    assert daily_bar_cutoff(datetime(2026, 7, 20, 6, 30)) == date(2026, 7, 19)
    assert daily_bar_cutoff(datetime(2026, 7, 20, 16, 30)) == date(2026, 7, 20)
    # a bare date is treated as midnight (conservative)
    assert daily_bar_cutoff(date(2026, 7, 17)) == date(2026, 7, 16)
