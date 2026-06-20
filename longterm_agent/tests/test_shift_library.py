"""
Integration tests for the shift library: write → grade → read, and the leakage +
version filters in find_similar_shifts. Uses synthetic TEST* rows, cleaned up after.
"""
from datetime import datetime, timedelta

import pytest

from longterm_agent.tools.shift_library_tool import write_shift_row, find_similar_shifts


TEST_TICKER = "ZZTEST"
VERSION = "testver"


@pytest.fixture
def seeded(db):
    """Insert a graded past shift and a too-recent (ungradable) one; clean up after."""
    past = datetime(2026, 3, 10)
    shift = {"direction": "negative", "magnitude_z": -1.4, "shift_score": 0.7, "inflection_date": "2026-03-05"}
    metrics = {"recent_buckets": 2, "new_stories": [{"label": "Export controls"}],
               "attention": {"spike_ratio": 1.1}, "narrative_turnover": 0.8}
    write_shift_row(db, TEST_TICKER, past, VERSION, shift, {}, metrics)
    # grade it (simulate the deferred grading job)
    import psycopg2
    conn = psycopg2.connect(**db.db_config)
    with conn.cursor() as cur:
        cur.execute("""UPDATE shift_library SET fwd_return_4w=-0.06, benchmark_return_4w=0.01,
                       outcome_filled_at=%s WHERE ticker=%s""", [past.date() + timedelta(days=28), TEST_TICKER])
    conn.commit()
    conn.close()
    yield
    conn = psycopg2.connect(**db.db_config)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM shift_library WHERE ticker=%s", [TEST_TICKER])
    conn.commit()
    conn.close()


def test_find_returns_graded_analog(db, seeded):
    res = find_similar_shifts(db, "OTHER", datetime(2026, 6, 15), VERSION,
                              direction="negative", target_z=-1.3, causal_types=["Export controls"], k=6)
    assert res["n_analogs"] == 1
    assert res["median_fwd_return_4w"] == -0.06


def test_leakage_excludes_unmatured_analog(db, seeded):
    # as_of only 10 days after the analog → its 4w outcome window hasn't closed → excluded
    res = find_similar_shifts(db, "OTHER", datetime(2026, 3, 20), VERSION,
                              direction="negative", target_z=-1.3, causal_types=["Export controls"], k=6)
    assert res["n_analogs"] == 0


def test_version_filter(db, seeded):
    # a different agent_version must not see this row
    res = find_similar_shifts(db, "OTHER", datetime(2026, 6, 15), "otherver",
                              direction="negative", target_z=-1.3, causal_types=[], k=6)
    assert res["n_analogs"] == 0


def test_direction_filter(db, seeded):
    # querying for a positive analog must not return the negative one
    res = find_similar_shifts(db, "OTHER", datetime(2026, 6, 15), VERSION,
                              direction="positive", target_z=1.3, causal_types=[], k=6)
    assert res["n_analogs"] == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
