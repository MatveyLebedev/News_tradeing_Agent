"""D2 — transaction costs and borrow, as a third book (docs/PIT_AUDIT_2026-07-19.md §D2).

Acceptance §7.4 requires verdicts to be net of turnover, and at the magnitudes this ledger
actually measures (+0.2…+0.9pp per 4w on the leak-free books) costs are decision-relevant
rather than cosmetic. They also fall UNEVENLY across an A/B: `sel_null`'s challenger trades
1.17–1.25× more, so a gross comparison silently favours the busier arm. That is the reason
this is a blocking dependency of `sel_null` stage 2 and is frozen BEFORE that run.

Model, deliberately flat and simple — a per-name microstructure model would be a second
unvalidated model sitting under a verdict:

    cost = stock round-trip + hedge round-trip + borrow (SHORT only, pro-rated by days held)

The hedge leg is charged because `excess` is not a bare stock return: it is the stock
against SPY, i.e. a pair. Scoring the pair's return while paying only the stock's costs
would understate what implementing it takes.

Defaults are conservative for the traded universe (mega-caps): a real half-spread on AAPL
is well under 1bp, so 5bp per side absorbs commission and market impact with room to spare.
`stress()` returns a deliberately harsh preset for sensitivity, NOT for verdicts — the
verdict preset is the default, frozen 2026-07-20.
"""
from typing import Optional

# --- frozen 2026-07-20, before the sel_null stage-2 holdout run ---------------------------
SPREAD_BPS_PER_SIDE = 5.0        # stock leg: half-spread + commission + impact, each side
HEDGE_SPREAD_BPS_PER_SIDE = 1.0  # SPY leg: far tighter than any single name
BORROW_ANNUAL_BPS = 50.0         # easy-to-borrow mega-cap short, annualised
DAYS_PER_YEAR = 365.0

BPS = 1e-4


def stress() -> dict:
    """A 4× harsher world, for sensitivity only. Never the basis of a verdict."""
    return {"spread_bps_per_side": 20.0, "hedge_spread_bps_per_side": 4.0,
            "borrow_annual_bps": 300.0}


def round_trip_cost(direction: str, holding_days: float,
                    spread_bps_per_side: float = SPREAD_BPS_PER_SIDE,
                    hedge_spread_bps_per_side: float = HEDGE_SPREAD_BPS_PER_SIDE,
                    borrow_annual_bps: float = BORROW_ANNUAL_BPS) -> float:
    """Total round-trip cost of one hedged position, as a positive decimal fraction.

    Always positive: it is subtracted from a return, never added to it.
    """
    if holding_days is None or holding_days < 0:
        raise ValueError(f"holding_days must be >= 0, got {holding_days!r}")
    stock = 2.0 * spread_bps_per_side * BPS
    hedge = 2.0 * hedge_spread_bps_per_side * BPS
    borrow = 0.0
    if (direction or "").upper() == "SHORT":
        borrow = borrow_annual_bps * BPS * (holding_days / DAYS_PER_YEAR)
    return stock + hedge + borrow


def net_excess(excess: Optional[float], direction: str, holding_days: float,
               **overrides) -> Optional[float]:
    """`excess` after costs. None in ⇒ None out, so ungraded rows stay ungraded."""
    if excess is None:
        return None
    return excess - round_trip_cost(direction, holding_days, **overrides)


def describe(**overrides) -> str:
    """One line naming the assumptions, so a printed book always carries its own model."""
    p = {"spread_bps_per_side": SPREAD_BPS_PER_SIDE,
         "hedge_spread_bps_per_side": HEDGE_SPREAD_BPS_PER_SIDE,
         "borrow_annual_bps": BORROW_ANNUAL_BPS, **overrides}
    return (f"costs: {p['spread_bps_per_side']:.0f}bp/side stock + "
            f"{p['hedge_spread_bps_per_side']:.0f}bp/side hedge + "
            f"{p['borrow_annual_bps']:.0f}bp/yr borrow on shorts")
