"""D2 — transaction costs and borrow (pure; no DB, no LLM).

Frozen before the sel_null stage-2 holdout run, so these tests pin the numbers a verdict
will later lean on. If a default changes, a test must change with it deliberately.
"""
import pytest

from longterm_agent import costs


def test_long_pays_both_legs_and_no_borrow():
    """5bp+5bp stock + 1bp+1bp hedge = 12bp, and a long never pays borrow."""
    c = costs.round_trip_cost("LONG", 28)
    assert c == pytest.approx(12 * 1e-4)
    assert costs.round_trip_cost("LONG", 28) == costs.round_trip_cost("LONG", 365)


def test_short_adds_borrow_prorated_by_days():
    c28 = costs.round_trip_cost("SHORT", 28)
    assert c28 == pytest.approx(12e-4 + 50e-4 * 28 / 365)
    # borrow is the ONLY term that scales with time held
    assert costs.round_trip_cost("SHORT", 56) - c28 == pytest.approx(50e-4 * 28 / 365)


def test_cost_is_always_positive_so_it_can_only_subtract():
    for d in ("LONG", "SHORT", "long", "short"):
        assert costs.round_trip_cost(d, 28) > 0


def test_direction_is_case_insensitive():
    assert costs.round_trip_cost("short", 28) == costs.round_trip_cost("SHORT", 28)


def test_net_excess_subtracts():
    assert costs.net_excess(0.0200, "LONG", 28) == pytest.approx(0.0200 - 12e-4)


def test_net_excess_preserves_ungraded_rows():
    """None must survive as None — an ungraded row is not a zero-return row."""
    assert costs.net_excess(None, "LONG", 28) is None


def test_negative_holding_days_is_rejected():
    with pytest.raises(ValueError):
        costs.round_trip_cost("LONG", -1)
    with pytest.raises(ValueError):
        costs.round_trip_cost("LONG", None)


def test_stress_preset_is_strictly_harsher_and_not_the_default():
    base = costs.round_trip_cost("SHORT", 28)
    assert costs.round_trip_cost("SHORT", 28, **costs.stress()) > base


def test_describe_names_every_assumption():
    """A printed book must carry its cost model, so the numbers can never float free."""
    d = costs.describe()
    assert "5bp/side stock" in d and "1bp/side hedge" in d and "50bp/yr borrow" in d


def test_costs_are_small_but_not_negligible_at_measured_magnitudes():
    """Sanity on the frozen scale: ~12-16bp, i.e. material against a +0.18% book."""
    assert 0.0010 < costs.round_trip_cost("LONG", 28) < 0.0020
    assert 0.0010 < costs.round_trip_cost("SHORT", 28) < 0.0020
