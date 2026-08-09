"""The backfill's two silent-failure surfaces: the free-tier date boundary and the table name."""
from datetime import date, datetime

import pytest

from bloomberg_news_fetcher.alpaca_backfill import (BENCHMARKS, check_end_date, table_name,
                                                    to_et)

TODAY = date(2026, 8, 9)


# --- the free-tier boundary --------------------------------------------------------------
def test_today_is_allowed():
    """Measured: end=2026-08-09 succeeds while 2026-08-10 does not. The first version of
    this check was a day too strict and refused the very date its own message suggested."""
    check_end_date("2026-08-09", TODAY)


def test_an_earlier_day_is_allowed():
    check_end_date("2026-04-01", TODAY)


def test_reaching_into_tomorrow_is_refused():
    with pytest.raises(SystemExit, match="current UTC day"):
        check_end_date("2026-08-10", TODAY)


def test_the_message_names_a_date_that_actually_works():
    """A guard that suggests a rejected value is worse than no guard."""
    with pytest.raises(SystemExit) as e:
        check_end_date("2026-08-20", TODAY)
    suggested = str(e.value).split("use ")[1].split(" ")[0]
    check_end_date(suggested, TODAY)          # must not raise


# --- table naming ---------------------------------------------------------------------
@pytest.mark.parametrize("ticker,expected", [
    ("SPY", "historical_data_spy"),
    ("XLRE", "historical_data_xlre"),
    ("BRK-B", "historical_data_brk_b"),      # the case that makes the rule non-trivial
    ("BRK.B", "historical_data_brk_b"),
])
def test_table_name_matches_the_live_fetcher(ticker, expected):
    """Pinned rather than compared against `price_fetcher.get_table_name` directly: that
    module opens with a flat `from logger import ...` that only resolves from inside its own
    package, so importing it here fails. The contract is the same one — lowercase, `-` and
    `.` to `_` — and two naming rules would mean two sets of tables with one always empty.
    """
    assert table_name(ticker) == expected


# --- timezone -----------------------------------------------------------------------------
def test_utc_bars_land_in_eastern():
    """A UTC row in these tables is 4-5h out of line with every other source, silently."""
    assert to_et("2026-08-07T13:30:00Z").strftime("%H:%M") == "09:30"


def test_the_session_close_converts_too():
    assert to_et("2026-08-07T19:59:00Z").strftime("%H:%M") == "15:59"


# --- symbol translation -----------------------------------------------------------------
@pytest.mark.parametrize("repo,wire", [("BRK-B", "BRK.B"), ("AAPL", "AAPL"), ("XYZ", "XYZ")])
def test_share_classes_use_alpacas_spelling(repo, wire):
    """Yahoo writes BRK-B, Alpaca wants BRK.B — and ONE unknown symbol makes the whole
    multi-symbol request 400, so a single stale ticker costs all 121."""
    from bloomberg_news_fetcher.alpaca_backfill import alpaca_symbol
    assert alpaca_symbol(repo) == wire


def test_the_wire_name_still_maps_back_to_our_table():
    from bloomberg_news_fetcher.alpaca_backfill import alpaca_symbol
    assert table_name("BRK-B") == table_name(alpaca_symbol("BRK-B")) == "historical_data_brk_b"
