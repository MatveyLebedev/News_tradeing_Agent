"""Unit tests for the pure backtest metrics (Backtest Plan B1) — no DB, deterministic."""
import math

from longterm_agent import backtest_metrics as M


def test_distribution_basic():
    d = M.distribution([0.0, 0.10, 0.20, 0.30, 0.40])
    assert d["n"] == 5
    assert abs(d["mean"] - 0.20) < 1e-9
    assert abs(d["median"] - 0.20) < 1e-9
    assert abs(d["p25"] - 0.10) < 1e-9
    assert abs(d["p75"] - 0.30) < 1e-9
    assert d["min"] == 0.0 and d["max"] == 0.40


def test_distribution_empty_and_none_safe():
    assert M.distribution([])["n"] == 0
    assert M.distribution([None, None])["n"] == 0
    assert M.distribution([0.1, None, 0.3])["n"] == 2


def test_win_rate():
    assert M.win_rate([0.1, -0.2, 0.3, 0.0]) == 0.5   # 0.0 is not a win
    assert M.win_rate([]) is None


def test_sharpe_known_value():
    s = M.sharpe([0.03, 0.01, 0.02, 0.04])  # mean .025, pstdev ~.01118
    assert s is not None and abs(s - 2.2360679) < 1e-4
    assert M.sharpe([0.02, 0.02, 0.02]) is None  # zero vol → None
    assert M.sharpe([0.05]) is None              # < 2 points
    assert abs(M.sharpe([0.1, -0.1, 0.1, -0.1])) < 1e-9  # zero mean → 0


def test_sharpe_annualized():
    base = M.sharpe([0.03, 0.01, 0.02, 0.04])
    ann = M.sharpe([0.03, 0.01, 0.02, 0.04], periods_per_year=52)
    assert abs(ann - base * math.sqrt(52)) < 1e-9


def test_max_drawdown():
    assert M.max_drawdown([0.1, -0.2, 0.05]) == -0.2   # peak +0.1 → trough -0.1
    assert M.max_drawdown([0.1, 0.1, 0.1]) == 0.0      # monotone up → no drawdown
    assert M.max_drawdown([]) is None


def test_calibration_curve_buckets():
    trades = [
        {"conviction": 0.60, "excess": 0.05},   # bucket 0.55-0.65, win
        {"conviction": 0.70, "excess": -0.02},  # bucket 0.65-0.75, loss
        {"conviction": 0.80, "excess": 0.10},   # bucket 0.75-0.85, win
        {"conviction": 0.82, "excess": -0.01},  # bucket 0.75-0.85, loss
    ]
    cur = M.calibration_curve(trades)
    by = {(round(b["lo"], 2), round(b["hi"], 2)): b for b in cur}
    assert by[(0.55, 0.65)]["n"] == 1 and by[(0.55, 0.65)]["win_rate"] == 1.0
    assert by[(0.65, 0.75)]["n"] == 1 and by[(0.65, 0.75)]["win_rate"] == 0.0
    assert by[(0.75, 0.85)]["n"] == 2 and by[(0.75, 0.85)]["win_rate"] == 0.5
    assert by[(0.85, 1.01)]["n"] == 0 and by[(0.85, 1.01)]["win_rate"] is None


def test_bootstrap_ci_deterministic_and_brackets():
    vals = [0.01, 0.02, 0.03, 0.04, 0.05]
    lo1, hi1 = M.bootstrap_ci(vals, lambda s: M.distribution(s)["mean"])
    lo2, hi2 = M.bootstrap_ci(vals, lambda s: M.distribution(s)["mean"])
    assert (lo1, hi1) == (lo2, hi2)          # deterministic given the seed
    assert lo1 < 0.03 < hi1                  # brackets the sample mean
    assert lo1 > 0 and hi1 < 0.06            # within the data range
    assert M.bootstrap_ci([0.1], M.win_rate) == (None, None)  # too few points
