"""
Integration tests for grade_shifts return math against real daily prices (NVDA/SPY).
Skips if the DB / daily bars are unavailable.
"""
from datetime import datetime

import psycopg2

from longterm_agent.grade_shifts import _close_on_or_before, _ret


def test_close_lookup_returns_price(db):
    conn = psycopg2.connect(**db.db_config)
    cur = conn.cursor()
    px = _close_on_or_before(cur, "NVDA", datetime(2026, 5, 15).date())
    conn.close()
    if px is None:
        import pytest
        pytest.skip("no NVDA daily bars in window")
    assert px > 0


def test_ret_is_a_fraction(db):
    conn = psycopg2.connect(**db.db_config)
    cur = conn.cursor()
    r = _ret(cur, "NVDA", datetime(2026, 4, 15).date(), 28)
    conn.close()
    if r is None:
        import pytest
        pytest.skip("insufficient NVDA daily bars")
    assert -0.9 < r < 5.0  # sane fractional return


def test_missing_ticker_returns_none(db):
    conn = psycopg2.connect(**db.db_config)
    cur = conn.cursor()
    r = _ret(cur, "NOTATICKER", datetime(2026, 4, 15).date(), 28)
    conn.close()
    assert r is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
