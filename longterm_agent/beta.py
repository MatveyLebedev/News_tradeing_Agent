"""Beta-adjusted book (P3, 2026-07-20) — separating stock-picking from factor exposure.

`excess` scores a trade against SPY at an implicit beta of 1. That flatters a high-beta
long in a rising week and punishes a defensive one for being defensive: in the
`monday_t1_pit` book, PEP and KO priced at beta ≈ −0.03 to SPY, so each carried a
mechanical ~−5pp before any judgement was involved. The beta-adjusted book asks the
narrower question — did the stock beat what its own market sensitivity predicted?

    excess_beta = sign * (stock_return − beta * benchmark_return)

which is exactly `stats.trade_excess` with beta in place of 1, so the two books stay
directly comparable. Same class of reporting addition as the sector-relative benchmark:
it changes what we *measure*, never what the agent decides.

Measured on the whole book, beta explained only ~4% of excess dispersion — the losses
are real stock-specific misses. It matters per trade, not in aggregate, which is
precisely why it belongs in the report rather than in a gate.

Beta is estimated from daily returns STRICTLY BEFORE the entry bar, so it is a property
knowable at decision time (grading may see the future by construction, but there is no
reason to let it).
"""
from datetime import timedelta
from typing import List, Optional, Sequence

DEFAULT_LOOKBACK_DAYS = 180
MIN_OBSERVATIONS = 30


def beta_from_returns(stock: Sequence[float], bench: Sequence[float]) -> Optional[float]:
    """OLS beta = cov(stock, bench) / var(bench). None when undefined or too short."""
    n = min(len(stock), len(bench))
    if n < MIN_OBSERVATIONS:
        return None
    s, b = list(stock[:n]), list(bench[:n])
    mb = sum(b) / n
    var = sum((x - mb) ** 2 for x in b)
    if var == 0:
        return None
    ms = sum(s) / n
    cov = sum((s[i] - ms) * (b[i] - mb) for i in range(n))
    return round(cov / var, 4)


def beta_adjusted_excess(direction: str, fwd, bench, beta) -> Optional[float]:
    """Strategy excess against a beta-matched benchmark. Mirrors stats.trade_excess."""
    if fwd is None or bench is None or beta is None or direction not in ("LONG", "SHORT"):
        return None
    raw = fwd - beta * bench
    return raw if direction == "LONG" else -raw


def _closes(cur, ticker: str, start, end) -> List[tuple]:
    cur.execute("""
        SELECT date, close FROM stock_prices_daily
        WHERE ticker=%s AND date >= %s AND date < %s AND close IS NOT NULL
        ORDER BY date
    """, [ticker.upper(), start, end])
    return [(d, float(c)) for d, c in cur.fetchall()]


def estimate_beta(cur, ticker: str, entry_date, benchmark: str = "SPY",
                  lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Optional[float]:
    """Beta of `ticker` vs `benchmark` over the daily bars before `entry_date`."""
    start = entry_date - timedelta(days=lookback_days)
    s = _closes(cur, ticker, start, entry_date)
    b = dict(_closes(cur, benchmark, start, entry_date))
    if len(s) < MIN_OBSERVATIONS + 1:
        return None
    rs, rb, prev = [], [], None
    for d, c in s:
        if prev and d in b and prev[0] in b and b[prev[0]]:
            rs.append(c / prev[1] - 1)
            rb.append(b[d] / b[prev[0]] - 1)
        prev = (d, c)
    return beta_from_returns(rs, rb)
