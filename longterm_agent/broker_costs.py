"""Real broker tariffs -> the bps the frozen cost model already speaks.

`costs.py` is deliberately flat: one number of bps per side, size- and price-independent,
frozen 2026-07-20 because verdicts rest on it. Real tariffs are none of those things —
they carry per-order minimums, per-share fees and percentage-of-notional terms, so the same
strategy costs a different number of bps on a $22 stock than on a $1021 one, and a different
number again at $5k than at $50k per leg.

This module does the conversion and nothing else. It never redefines the model: it produces
`round_trip_cost()` kwargs, so the frozen arithmetic stays the single implementation and
`costs.py` stays byte-identical.

    from longterm_agent import costs, broker_costs
    ov = broker_costs.overrides("ffglobal_standard", price=140.06, notional=10_000)
    net = costs.net_excess(excess, "LONG", 28, **ov)

Measured on the intraday_curve34 book (121 real trades, real entry prices) the spread between
these presets is not cosmetic: a hedged round trip costs ~0.11pp at just2trade and ~0.43pp at
ffglobal_standard, against a gross edge of +0.2…+0.9pp per 4 weeks.
"""
from typing import Optional

# --- tariffs ------------------------------------------------------------------------------
# pct_of_notional : fraction of trade value, per order
# per_share       : USD per share, per order
# per_order       : flat USD per order, charged on top
# min_per_order   : USD floor per order (applied to the per_share term)
# borrow_annual_bps: cost of borrowing stock for a SHORT, annualised
#
# `borrow_annual_bps=None` means UNVERIFIED, not zero. Costing a SHORT against an unverified
# borrow rate is how a verdict silently inherits a guess, so the code refuses rather than
# picks a number — see `overrides()`.
TARIFFS = {
    # 0.12% + $1.2/order, balance from $500. Securities borrow 15%/yr per the broker's
    # margin-trading schedule — 30x the frozen BORROW_ANNUAL_BPS, and decision-relevant.
    "ffglobal_standard": dict(pct_of_notional=0.0012, per_share=0.0, per_order=1.20,
                              min_per_order=0.0, borrow_annual_bps=1500.0),
    # $0.012/share, $2.75 minimum. Borrow rate never published — must be supplied.
    "just2trade": dict(pct_of_notional=0.0, per_share=0.012, per_order=0.0,
                       min_per_order=2.75, borrow_annual_bps=None),
    # ~0.10% of turnover.
    "centras": dict(pct_of_notional=0.0010, per_share=0.0, per_order=0.0,
                    min_per_order=0.0, borrow_annual_bps=None),
    # Reference point only — unavailable to a Russian tax resident. General-collateral
    # borrow on liquid mega-caps runs ~0.25%/yr, which is why the frozen 50bps default is
    # honest for a discount broker and wrong for the ones actually reachable.
    "ibkr_fixed": dict(pct_of_notional=0.0, per_share=0.005, per_order=0.0,
                       min_per_order=1.00, borrow_annual_bps=25.0),
    # Zero commission; only pass-through regulatory fees, and those on SELLS ONLY:
    # SEC $27.80 per $1M principal, FINRA TAF $0.000166/share capped at $8.30/order.
    # `sell_only` halves them into the model's per-side average (see bps_per_side).
    # Borrow is $0 on easy-to-borrow names, and hard-to-borrow is not shortable at all,
    # so any short this book can actually place costs nothing to carry.
    # LIVE trading here is out of reach on a Russian tax residency — the paper account is.
    # It is in the table as the venue the paper run executes on, so "cost of the simulator"
    # can be told apart from "cost of the broker I could actually use".
    "alpaca_live": dict(pct_of_notional=0.0000278, per_share=0.000166, per_order=0.0,
                        min_per_order=0.0, borrow_annual_bps=0.0, sell_only=True),
}

BPS = 1e-4


def order_cost_usd(broker: str, price: float, notional: float) -> float:
    """What ONE order costs in dollars under this tariff."""
    if price is None or price <= 0:
        raise ValueError(f"price must be > 0, got {price!r}")
    if notional is None or notional <= 0:
        raise ValueError(f"notional must be > 0, got {notional!r}")
    t = TARIFFS[broker]
    shares = notional / price
    variable = max(t["per_share"] * shares, t["min_per_order"])
    if t.get("taf_cap_usd"):
        variable = min(variable, t["taf_cap_usd"])
    return t["pct_of_notional"] * notional + variable + t["per_order"]


def bps_per_side(broker: str, price: float, notional: float) -> float:
    """The same order expressed the way the frozen model speaks: bps of the position.

    `round_trip_cost` charges this twice, so for a tariff that only bites on one side of the
    trip — regulatory fees are levied on sells, not buys — the honest per-side figure is the
    round-trip cost averaged over the two orders. It is an average, not a literal per-order
    charge; the round trip it produces is exact either way.
    """
    usd = order_cost_usd(broker, price, notional)
    if TARIFFS[broker].get("sell_only"):
        usd /= 2.0
    return usd / notional / BPS


def overrides(broker: str, price: float, notional: float,
              hedge_price: Optional[float] = None,
              hedge_notional: Optional[float] = None,
              borrow_annual_bps: Optional[float] = None,
              hedged: bool = True) -> dict:
    """kwargs for `costs.round_trip_cost` under this tariff.

    `hedge_notional` defaults to the stock notional — pass `beta * notional` to charge the
    beta-matched hedge the convention actually calls for. `hedged=False` zeroes the hedge leg
    for an unhedged book, but note that an unhedged book no longer earns `excess` at all, so
    the metric has to change with it (docs/BENCHMARK_CONVENTION.md).
    """
    if broker not in TARIFFS:
        raise KeyError(f"unknown broker {broker!r}; known: {sorted(TARIFFS)}")
    stock_bps = bps_per_side(broker, price, notional)

    hedge_bps = 0.0
    if hedged:
        # SPY is a high-priced, highly liquid instrument, but the tariff does not care:
        # a percentage fee charges the hedge exactly as much as the stock, which is why
        # hedging roughly doubles the bill at ffglobal_standard and barely moves it at ibkr.
        hedge_bps = bps_per_side(broker, hedge_price or price,
                                 hedge_notional or notional) * ((hedge_notional or notional)
                                                                / notional)

    borrow = borrow_annual_bps if borrow_annual_bps is not None \
        else TARIFFS[broker]["borrow_annual_bps"]
    if borrow is None:
        borrow = 0.0   # placeholder; `round_trip_cost` only reads it for SHORT (see below)

    return {"spread_bps_per_side": stock_bps,
            "hedge_spread_bps_per_side": hedge_bps,
            "borrow_annual_bps": borrow}


def require_borrow(broker: str, direction: str, borrow_annual_bps: Optional[float]) -> None:
    """Refuse to cost a SHORT against a borrow rate nobody has verified.

    Call this before pricing a short book. It exists because the alternative — defaulting an
    unknown rate to zero — produces a number that looks like a result and is one.
    """
    if (direction or "").upper() != "SHORT":
        return
    known = borrow_annual_bps if borrow_annual_bps is not None \
        else TARIFFS[broker]["borrow_annual_bps"]
    if known is None:
        raise ValueError(
            f"{broker} publishes no securities-borrow rate and none was supplied. A SHORT "
            f"cannot be costed without it: at ffglobal_standard's 15%/yr the borrow alone is "
            f"~1.15pp over 28 days, larger than the entire measured edge. Pass "
            f"borrow_annual_bps=... once the broker confirms it.")


def describe(broker: str, price: float, notional: float, **kw) -> str:
    """One line naming the assumptions, so a printed book carries its own model."""
    ov = overrides(broker, price, notional, **kw)
    t = TARIFFS[broker]
    borrow = "UNVERIFIED" if t["borrow_annual_bps"] is None else f"{t['borrow_annual_bps']:.0f}bps/yr"
    return (f"{broker}: {ov['spread_bps_per_side']:.2f}bps/side stock, "
            f"{ov['hedge_spread_bps_per_side']:.2f}bps/side hedge, borrow {borrow} "
            f"(at ${price:.2f}/share, ${notional:,.0f} notional)")
