"""Per-ticker `as_of`: fresher data cutoff, same cohort."""
from datetime import datetime

from longterm_agent.batch_run_longterm import decision_moment


def test_live_run_gets_the_current_moment():
    batch = datetime(2026, 8, 10, 12, 0, 0)
    now = datetime(2026, 8, 10, 13, 29, 0)      # the last ticker of a long batch
    assert decision_moment(batch, now) == now


def test_first_ticker_is_barely_after_the_batch_start():
    batch = datetime(2026, 8, 10, 12, 0, 0)
    now = datetime(2026, 8, 10, 12, 1, 30)
    assert decision_moment(batch, now) == now


def test_historical_replay_is_never_refreshed():
    """`--as-of 2026-06-15` must stay a replay, not become a live run on today's data."""
    batch = datetime(2026, 6, 15, 12, 0, 0)
    now = datetime(2026, 8, 10, 12, 5, 0)
    assert decision_moment(batch, now) == batch


def test_midnight_crossing_keeps_the_cohort_whole():
    """A later date would split one cohort in two and break the date-keyed resume set."""
    batch = datetime(2026, 8, 10, 23, 50, 0)
    now = datetime(2026, 8, 11, 0, 3, 0)
    assert decision_moment(batch, now) == batch


def test_the_cohort_date_never_moves():
    batch = datetime(2026, 8, 10, 12, 0, 0)
    for minute in (1, 45, 90, 200):
        now = datetime(2026, 8, 10, 12, 0, 0).replace(hour=12 + minute // 60,
                                                      minute=minute % 60)
        assert decision_moment(batch, now).date() == batch.date()


def test_still_in_session_for_a_midday_batch():
    """The whole point: a late ticker must still resolve to mid_1m, not fall to open_t1."""
    from longterm_agent.exit_rules import in_session
    batch = datetime(2026, 8, 10, 12, 0, 0)
    assert in_session(decision_moment(batch, datetime(2026, 8, 10, 13, 29, 0)))
    # …and an overrun past the close correctly is NOT in session — that is the mixed-cohort
    # case the guard still has to catch, not something this function should paper over.
    assert not in_session(decision_moment(batch, datetime(2026, 8, 10, 16, 30, 0)))
