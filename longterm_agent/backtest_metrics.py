"""
Pure metrics for the walk-forward backtest report (Backtest Plan B1).

No DB, no I/O — every function takes plain numbers/dicts, so the math is unit-tested in
test_backtest_metrics.py. Vocabulary mirrors stats.py: a *trade* is a gated directional signal;
*excess* is the signed strategy return vs benchmark (LONG: stock−bench, SHORT: bench−stock); a
*win* is excess > 0. These exist because a mean alone — on a tiny, correlated, single-regime
sample — hides everything that matters: the spread, the tails, and how little we actually know
(hence the bootstrap CIs and the min-n gate in the report).
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Number = float


def _quantile(sorted_xs: List[float], q: float) -> Optional[float]:
    """Linear-interpolation quantile on an ALREADY-sorted list."""
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = q * (len(sorted_xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_xs[lo]
    frac = pos - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def distribution(values: Sequence[Number]) -> Dict[str, Optional[float]]:
    """n / mean / median / IQR (p25,p75) / min / max / std of a sample (ignores Nones)."""
    xs = [v for v in values if v is not None]
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None,
                "min": None, "max": None, "std": None}
    s = sorted(xs)
    return {
        "n": n,
        "mean": statistics.fmean(xs),
        "median": statistics.median(xs),
        "p25": _quantile(s, 0.25),
        "p75": _quantile(s, 0.75),
        "min": s[0],
        "max": s[-1],
        "std": statistics.pstdev(xs) if n > 1 else 0.0,
    }


def win_rate(excesses: Sequence[Number]) -> Optional[float]:
    xs = [e for e in excesses if e is not None]
    if not xs:
        return None
    return sum(1 for e in xs if e > 0) / len(xs)


def sharpe(returns: Sequence[Number], periods_per_year: Optional[float] = None) -> Optional[float]:
    """Mean/stdev of per-period returns; annualized by sqrt(periods_per_year) when given.
    Computed on *excess* returns this is the information ratio. None if < 2 points or zero vol."""
    xs = [r for r in returns if r is not None]
    if len(xs) < 2:
        return None
    sd = statistics.pstdev(xs)
    if sd == 0:
        return None
    s = statistics.fmean(xs) / sd
    return s * math.sqrt(periods_per_year) if periods_per_year else s


def max_drawdown(per_period_returns: Sequence[Number]) -> Optional[float]:
    """Max peak-to-trough drop of the cumulative (additive) excess equity curve. Non-positive
    (0 = monotone up / single point). Additive because excess returns aggregate additively here."""
    xs = [r for r in per_period_returns if r is not None]
    if not xs:
        return None
    cum = peak = mdd = 0.0
    for r in xs:
        cum += r
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return mdd


def calibration_curve(
    trades: Sequence[Dict],
    edges: Sequence[float] = (0.55, 0.65, 0.75, 0.85, 1.01),
) -> List[Dict]:
    """Reliability curve: bucket trades by conviction → win-rate + mean excess per bucket.
    A predictive conviction gives a monotonically rising win-rate (the §3.1 test).
    `trades`: dicts with 'conviction' and 'excess'. Buckets are [edges[i], edges[i+1])."""
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        ex = [t["excess"] for t in trades
              if t.get("conviction") is not None and t.get("excess") is not None
              and lo <= t["conviction"] < hi]
        out.append({
            "lo": lo, "hi": hi, "n": len(ex),
            "win_rate": win_rate(ex),
            "mean_excess": statistics.fmean(ex) if ex else None,
        })
    return out


def bootstrap_ci(
    values: Sequence[Number],
    stat_fn: Callable[[Sequence[Number]], Optional[float]],
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 1234,
) -> Tuple[Optional[float], Optional[float]]:
    """Percentile bootstrap CI for an arbitrary statistic of `values`. Deterministic given seed
    (so a report re-run is byte-stable, matching B0). Returns (lo, hi) or (None, None)."""
    xs = [v for v in values if v is not None]
    if len(xs) < 2:
        return (None, None)
    rng = random.Random(seed)
    n = len(xs)
    stats: List[float] = []
    for _ in range(n_boot):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        s = stat_fn(sample)
        if s is not None:
            stats.append(s)
    if not stats:
        return (None, None)
    stats.sort()
    return (_quantile(stats, alpha / 2), _quantile(stats, 1 - alpha / 2))
