"""A cohort's rows must be decided on the cohort's own day."""
from datetime import date, datetime

from longterm_agent.batch_run_longterm import lagged_decisions


def _row(ticker, as_of_day, decided_day, hour=12):
    return (ticker, date(2026, 7, as_of_day),
            datetime(2026, 7, decided_day, hour, 0))


def test_a_same_day_cohort_is_clean():
    rows = [_row("AAPL", 26, 26, hour=6), _row("MSFT", 26, 26, hour=7)]
    assert lagged_decisions(rows) == []


def test_a_resumed_row_is_caught():
    """The 2026-07-26 case: the batch stalled and was re-run two days later."""
    rows = [_row("AAPL", 26, 26, hour=6), _row("AVGO", 26, 28)]
    assert lagged_decisions(rows) == [("AVGO", 2)]


def test_every_lagged_row_is_reported_with_its_lag():
    rows = [_row("A", 26, 28), _row("B", 26, 27), _row("C", 26, 26, hour=6)]
    assert sorted(lagged_decisions(rows)) == [("A", 2), ("B", 1)]


def test_a_decision_before_the_cohort_is_also_flagged():
    """Negative lag is just as wrong — it would mean the row predates its own cohort."""
    assert lagged_decisions([_row("AAPL", 26, 24)]) == [("AAPL", -2)]


def test_nulls_are_skipped_not_counted():
    rows = [("AAPL", date(2026, 7, 26), None), (None, None, None)]
    assert lagged_decisions(rows) == []


def test_an_empty_cohort_is_clean():
    assert lagged_decisions([]) == []


def test_the_real_incident_reproduces():
    """Five rows on the Sunday, fifty-nine on the Tuesday — what actually happened."""
    rows = ([_row(f"S{i}", 26, 26, hour=6) for i in range(5)]
            + [_row(f"T{i}", 26, 28) for i in range(59)])
    out = lagged_decisions(rows)
    assert len(out) == 59 and {lag for _, lag in out} == {2}
