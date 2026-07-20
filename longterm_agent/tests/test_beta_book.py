"""P3 — beta-adjusted book (pure; no DB).

`excess` scores every trade at an implicit beta of 1. These tests pin the two things
that matter: the estimator, and the sign convention that keeps the beta book directly
comparable with `stats.trade_excess`.
"""
import pytest

from longterm_agent.beta import (MIN_OBSERVATIONS, beta_adjusted_excess,
                                 beta_from_returns)
from longterm_agent.stats import trade_excess


def test_beta_of_a_perfect_double_is_two():
    bench = [0.01, -0.02, 0.015, -0.005] * 10
    stock = [2 * x for x in bench]
    assert beta_from_returns(stock, bench) == pytest.approx(2.0, abs=1e-6)


def test_beta_of_the_benchmark_itself_is_one():
    bench = [0.01, -0.02, 0.015, -0.005] * 10
    assert beta_from_returns(bench, bench) == pytest.approx(1.0, abs=1e-6)


def test_beta_none_when_too_few_observations_or_flat_benchmark():
    assert beta_from_returns([0.01] * 5, [0.01] * 5) is None
    assert beta_from_returns([0.01] * 40, [0.0] * 40) is None       # var(bench) == 0
    assert len([0.0] * MIN_OBSERVATIONS) == MIN_OBSERVATIONS         # guard is a real bound


def test_beta_one_reduces_to_plain_excess():
    """The books must be comparable: beta=1 IS the existing definition."""
    for direction in ("LONG", "SHORT"):
        assert (beta_adjusted_excess(direction, 0.08, 0.05, 1.0)
                == pytest.approx(trade_excess(direction, 0.08, 0.05)))


def test_defensive_long_is_no_longer_punished_for_low_beta():
    # KO-like: beta 0, stock +1% while SPY +5%. Plain excess says -4%; beta-adjusted
    # says the stock did what a beta-0 name should and then some.
    assert trade_excess("LONG", 0.01, 0.05) == pytest.approx(-0.04)
    assert beta_adjusted_excess("LONG", 0.01, 0.05, 0.0) == pytest.approx(+0.01)


def test_high_beta_long_loses_its_free_ride():
    # AVGO-like: beta 2, stock +10% while SPY +5% — plain excess credits +5%,
    # but beta alone predicted +10%, so the stock-picking contribution is zero.
    assert trade_excess("LONG", 0.10, 0.05) == pytest.approx(+0.05)
    assert beta_adjusted_excess("LONG", 0.10, 0.05, 2.0) == pytest.approx(0.0)


def test_short_sign_is_mirrored():
    # SHORT profits when the stock lags its beta-implied return
    assert beta_adjusted_excess("SHORT", 0.02, 0.05, 1.5) == pytest.approx(0.055)


def test_none_inputs_degrade_quietly():
    assert beta_adjusted_excess("LONG", None, 0.05, 1.0) is None
    assert beta_adjusted_excess("LONG", 0.05, None, 1.0) is None
    assert beta_adjusted_excess("LONG", 0.05, 0.05, None) is None
    assert beta_adjusted_excess("NEUTRAL", 0.05, 0.05, 1.0) is None
