"""PIT cutoff helpers and the shared date scanner (2026-07-19 leakage audit).

Pure — no DB, no graph. These encode the two rules the audit found violated:

  * a document dated D is not knowable before D's session closes (the `open_d0` regime
    made this a POST-ENTRY leak: decide 06:30, fill 09:30, read a 16:30 filing);
  * our own snapshots are knowable from the moment WE pulled them, which is why they use
    an ingestion timestamp rather than a session-close rule — the live Sunday batch runs
    an hour after the Sunday fetch and must still see it.
"""
from datetime import date, datetime

import pytz

from longterm_agent.tools.pit import (as_moment, document_cutoff, ingest_cutoff,
                                      max_moment)

ET = pytz.timezone("America/New_York")


def _utc_naive(et_naive):
    """The UTC-naive timestamp Postgres would store for an ET wall-clock moment."""
    return ET.localize(et_naive).astimezone(pytz.UTC).replace(tzinfo=None)


# --------------------------------------------------------------------- document cutoff
def test_document_cutoff_hides_same_day_filing_from_a_preopen_decision():
    # The monday_t1 regime: as_of Monday 06:30, fill at Monday's 09:30 open. A filing
    # dated Monday may be accepted at 16:30 — after the position exists.
    assert document_cutoff(datetime(2026, 7, 20, 6, 30)) == date(2026, 7, 19)


def test_document_cutoff_lets_a_post_close_run_see_the_same_day():
    assert document_cutoff(datetime(2026, 7, 20, 16, 30)) == date(2026, 7, 20)


def test_document_cutoff_midnight_cohort_excludes_its_own_day():
    # Standard grid cohorts are midnight stamps: nothing filed that day was knowable.
    assert document_cutoff(datetime(2026, 7, 17, 0, 0)) == date(2026, 7, 16)
    assert document_cutoff(date(2026, 7, 17)) == date(2026, 7, 16)


# ----------------------------------------------------------------------- ingest cutoff
def test_ingest_cutoff_converts_et_decision_moment_to_utc():
    # created_at is written by NOW() under the UTC session; as_of is ET wall clock.
    assert ingest_cutoff(datetime(2026, 7, 20, 6, 30)) == datetime(2026, 7, 20, 10, 30)
    assert ingest_cutoff(datetime(2026, 1, 20, 6, 30)) == datetime(2026, 1, 20, 11, 30)  # EST


def test_ingest_cutoff_keeps_the_same_morning_weekly_fetch_visible():
    """No live regression: the Sunday 06:00 batch still sees the 05:00 fundamentals pull."""
    bound = ingest_cutoff(datetime(2026, 7, 19, 6, 0))
    assert _utc_naive(datetime(2026, 7, 19, 5, 0)) <= bound      # fetched before → visible
    assert _utc_naive(datetime(2026, 7, 19, 7, 0)) > bound       # fetched after  → hidden


def test_ingest_cutoff_accepts_bare_dates_and_aware_datetimes():
    assert ingest_cutoff(date(2026, 7, 20)) == datetime(2026, 7, 20, 4, 0)  # ET midnight
    assert ingest_cutoff(ET.localize(datetime(2026, 7, 20, 6, 30))) == \
        datetime(2026, 7, 20, 10, 30)


# ------------------------------------------------------------------------ date scanner
def test_as_moment_parses_only_strings_that_are_entirely_a_timestamp():
    assert as_moment("2026-07-20") == datetime(2026, 7, 20)
    assert as_moment("2026-07-20T14:05:00") == datetime(2026, 7, 20, 14, 5)
    # prose that merely mentions a date must not be read as one, or the raw-Cypher guard
    # would drop rows for describing a quarter
    assert as_moment("guidance for 2026-07-20 was cut") is None
    assert as_moment("Q3 outlook") is None
    assert as_moment(42) is None
    assert as_moment(None) is None


def test_as_moment_normalises_aware_values_to_et():
    assert as_moment("2026-07-20T14:00:00+00:00") == datetime(2026, 7, 20, 10, 0)
    assert as_moment("2026-07-20T14:00:00Z") == datetime(2026, 7, 20, 10, 0)


def test_max_moment_walks_nested_payloads_and_dict_keys():
    payload = {"rows": [{"name": "x", "date": "2026-07-18T10:00:00"},
                        {"name": "y", "nested": {"when": "2026-07-21T09:30:00"}}]}
    assert max_moment(payload) == datetime(2026, 7, 21, 9, 30)
    assert max_moment({"2026-08-01": 1.0}) == datetime(2026, 8, 1)  # date-keyed series
    assert max_moment({"n": 3, "label": "no dates here"}) is None
    assert max_moment([]) is None
