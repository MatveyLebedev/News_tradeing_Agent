"""The cohort must not straddle the session boundary."""
from datetime import datetime

import pytest

from longterm_agent.batch_run_longterm import (PER_TICKER_MINUTES, convention_split,
                                               session_guard)

MIDDAY = datetime(2026, 8, 10, 12, 0)      # a Monday-noon batch
SUNDAY = datetime(2026, 8, 9, 6, 0)        # today's out-of-session book


# --- preventive -------------------------------------------------------------------------
def test_early_in_the_session_the_batch_runs():
    may, why = session_guard(MIDDAY, datetime(2026, 8, 10, 12, 30))
    assert may and why is None


def test_it_stops_when_the_close_is_too_near():
    """Not enough time to finish one more ticker before 16:00."""
    may, why = session_guard(MIDDAY, datetime(2026, 8, 10, 15, 59))
    assert not may and "min of session left" in why


def test_it_stops_once_the_session_is_over():
    may, why = session_guard(MIDDAY, datetime(2026, 8, 10, 16, 5))
    assert not may and "session closed" in why


def test_the_boundary_is_one_ticker_wide():
    """At exactly PER_TICKER_MINUTES of runway the next ticker is still allowed."""
    at = datetime(2026, 8, 10, 16, 0) - __import__("datetime").timedelta(
        minutes=PER_TICKER_MINUTES)
    assert session_guard(MIDDAY, at)[0]


def test_an_out_of_session_batch_is_never_stopped():
    """The Sunday 06:00 book resolves to one convention throughout — nothing to protect,
    and stopping it at 16:00 would be nonsense."""
    for now in (datetime(2026, 8, 9, 6, 30), datetime(2026, 8, 9, 20, 0)):
        assert session_guard(SUNDAY, now) == (True, None)


# --- detective ---------------------------------------------------------------------------
def test_a_clean_in_session_cohort_is_not_flagged():
    stamps = [datetime(2026, 8, 10, 12, 2), datetime(2026, 8, 10, 13, 40)]
    assert not convention_split(stamps)


def test_a_clean_out_of_session_cohort_is_not_flagged():
    stamps = [datetime(2026, 8, 9, 6, 1), datetime(2026, 8, 9, 7, 33)]
    assert not convention_split(stamps)


def test_a_straddling_cohort_is_flagged():
    """One row before the close and one after — two conventions in one book."""
    stamps = [datetime(2026, 8, 10, 15, 55), datetime(2026, 8, 10, 16, 10)]
    assert convention_split(stamps)


def test_nulls_do_not_create_a_phantom_split():
    stamps = [datetime(2026, 8, 10, 12, 2), None, datetime(2026, 8, 10, 12, 9)]
    assert not convention_split(stamps)


def test_an_empty_cohort_is_not_a_split():
    assert not convention_split([])
