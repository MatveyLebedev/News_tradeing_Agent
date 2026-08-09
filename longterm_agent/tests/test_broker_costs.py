import pytest

from longterm_agent import costs, broker_costs as bc


def test_percentage_tariff_is_price_independent():
    """ffglobal charges a percentage, so share price cannot change the bps."""
    assert bc.bps_per_side("ffglobal_standard", 200, 10_000) == \
           pytest.approx(bc.bps_per_side("ffglobal_standard", 22, 10_000))


def test_per_share_tariff_punishes_cheap_stock():
    """just2trade charges per share, so a $22 name costs more bps than a $200 one."""
    cheap = bc.bps_per_side("just2trade", 22, 10_000)
    dear = bc.bps_per_side("just2trade", 200, 10_000)
    assert cheap > dear
    # $200: 50 shares * $0.012 = $0.60, below the $2.75 floor -> the floor binds.
    assert dear == pytest.approx(2.75)


def test_minimum_stops_binding_on_a_large_order():
    """Above ~229 shares the per-share term overtakes the $2.75 minimum."""
    assert bc.bps_per_side("just2trade", 200, 50_000) < bc.bps_per_side("just2trade", 200, 10_000)


def test_percentage_tariff_has_a_floor_no_size_can_beat():
    """0.12% per side is 12bps however large the order — the flat $1.2 amortises, the pct does not."""
    huge = bc.bps_per_side("ffglobal_standard", 200, 10_000_000)
    assert huge == pytest.approx(12.0, abs=0.01)


def test_overrides_feed_the_frozen_model_unchanged():
    ov = bc.overrides("just2trade", price=200, notional=10_000)
    rt = costs.round_trip_cost("LONG", 28, **ov)
    # two stock orders + two hedge orders, all at the $2.75 floor on a $10k leg
    assert rt == pytest.approx(4 * 2.75 / 10_000)


def test_unhedged_drops_the_hedge_leg():
    hedged = costs.round_trip_cost("LONG", 28, **bc.overrides("ffglobal_standard", 200, 10_000))
    bare = costs.round_trip_cost("LONG", 28,
                                 **bc.overrides("ffglobal_standard", 200, 10_000, hedged=False))
    assert bare == pytest.approx(hedged / 2)


def test_beta_scales_the_hedge_leg():
    """A beta-1.34 name hedges with a larger SPY notional, so it costs more."""
    flat = bc.overrides("ffglobal_standard", 140.06, 10_000, hedge_price=600.0)
    lifted = bc.overrides("ffglobal_standard", 140.06, 10_000, hedge_price=600.0,
                          hedge_notional=1.3426 * 10_000)
    assert lifted["hedge_spread_bps_per_side"] > flat["hedge_spread_bps_per_side"]


def test_short_borrow_dominates_at_ffglobal():
    """15%/yr over 28 days is ~1.15pp — larger than the book's entire measured edge."""
    ov = bc.overrides("ffglobal_standard", 200, 10_000)
    borrow_only = costs.round_trip_cost("SHORT", 28, **ov) - costs.round_trip_cost("LONG", 28, **ov)
    assert borrow_only == pytest.approx(0.0115, abs=0.0005)


def test_unverified_borrow_refuses_to_price_a_short():
    with pytest.raises(ValueError, match="no securities-borrow rate"):
        bc.require_borrow("just2trade", "SHORT", None)


def test_unverified_borrow_is_fine_for_a_long():
    bc.require_borrow("just2trade", "LONG", None)      # must not raise


def test_supplied_borrow_unblocks_the_short():
    bc.require_borrow("just2trade", "SHORT", 50.0)     # must not raise


def test_unknown_broker_is_loud():
    with pytest.raises(KeyError):
        bc.overrides("definitely_not_a_broker", 200, 10_000)


@pytest.mark.parametrize("bad", [0, -1, None])
def test_rejects_nonsense_inputs(bad):
    with pytest.raises(ValueError):
        bc.order_cost_usd("just2trade", bad, 10_000)
    with pytest.raises(ValueError):
        bc.order_cost_usd("just2trade", 200, bad)
