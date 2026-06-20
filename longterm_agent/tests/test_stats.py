"""
Pure-logic tests for the stats win-rate math (the number the Telegram bot reports).
No DB needed — exercises trade_excess + compute_trade_stats directly.
"""
from longterm_agent.stats import trade_excess, compute_trade_stats


def test_long_excess_beats_benchmark():
    # stock +10%, SPY +4% → LONG excess +6%
    assert round(trade_excess("LONG", 0.10, 0.04), 4) == 0.06


def test_short_excess_inverts_sign():
    # stock -5%, SPY +3% → SHORT profits: excess = +8%
    assert round(trade_excess("SHORT", -0.05, 0.03), 4) == 0.08


def test_excess_none_when_ungraded():
    assert trade_excess("LONG", None, 0.04) is None
    assert trade_excess("LONG", 0.10, None) is None
    assert trade_excess("NEUTRAL", 0.10, 0.04) is None


def test_compute_trade_stats_winrate_and_excess():
    rows = [
        {"verdict": "LONG", "fwd_return_4w": 0.10, "benchmark_return_4w": 0.04},   # +6 win
        {"verdict": "LONG", "fwd_return_4w": 0.00, "benchmark_return_4w": 0.04},   # -4 loss
        {"verdict": "SHORT", "fwd_return_4w": -0.05, "benchmark_return_4w": 0.03}, # +8 win
        {"verdict": "LONG", "fwd_return_4w": None, "benchmark_return_4w": 0.04},   # ungraded → ignored
    ]
    s = compute_trade_stats(rows)
    assert s["graded"] == 3
    assert s["wins"] == 2
    assert round(s["win_rate"], 3) == round(2 / 3, 3)
    # mean of (0.06, -0.04, 0.08) = 0.0333...
    assert round(s["mean_excess"], 4) == 0.0333
    assert round(s["median_excess"], 4) == 0.06


def test_compute_trade_stats_empty():
    s = compute_trade_stats([])
    assert s["graded"] == 0
    assert s["win_rate"] is None
    assert s["mean_excess"] is None
