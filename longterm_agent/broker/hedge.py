"""One netted SPY position for the whole book, not a hedge leg per trade.

The scoring convention beta-matches every trade against SPY individually
(docs/BENCHMARK_CONVENTION.md). Holding that as ~24 separate small SPY shorts would be
arithmetically identical in aggregate — the exposure is Sigma(side x beta x notional) either
way — while being worse in every practical respect: 24 orders instead of one, and each of
them rounded to whole shares, which at $4,000 x beta 0.48 over a ~$700 instrument is under
three shares and rounds catastrophically.

Netting keeps the aggregate exact and makes rounding negligible. It costs nothing in the
display either: `/open` computes each position's `excess_now` from prices, never from a
hedge position, so per-trade attribution is unaffected.

A direction-balanced book largely self-hedges — the longs' and shorts' beta cancel — so the
netted position is usually small and sometimes zero. Zero is a correct answer here, not a
missing one.
"""
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from longterm_agent.broker import ledger

HEDGE_SYMBOL = "SPY"


def signed_beta_notional(rows: Iterable[Dict[str, Any]]) -> float:
    """Sigma(side x beta x notional) over the open book — the market exposure to neutralise.

    `side` is +1 LONG / -1 SHORT, matching the convention doc. A row without a fitted beta
    counts at 1.0 rather than being dropped: BENCHMARK_CONVENTION keeps such trades in the
    sample at beta = 1.0 so the denominator never changes silently, and the hedge has to
    follow the same rule or it would neutralise a different book than the one being scored.
    """
    total = 0.0
    for r in rows:
        side = 1.0 if (r.get("verdict") or "").upper() == "LONG" else -1.0
        beta = r.get("beta")
        beta = 1.0 if beta is None else float(beta)
        total += side * beta * float(r["notional"])
    return total


def target_shares(rows: Iterable[Dict[str, Any]], spy_price: float) -> int:
    """Whole SPY shares the book should hold. Negative = short, which is the usual case.

    Rounded toward zero: over-hedging introduces exposure the book never asked for, while
    under-hedging leaves a fraction of one share — the smaller error of the two.
    """
    if spy_price is None or spy_price <= 0:
        raise ValueError(f"spy_price must be > 0, got {spy_price!r}")
    exposure = signed_beta_notional(rows)
    return -int(math.trunc(exposure / spy_price))


def current_shares(positions: List[Dict[str, Any]]) -> int:
    """Signed SPY holding as the broker reports it (negative when short)."""
    for p in positions:
        if (p.get("symbol") or "").upper() == HEDGE_SYMBOL:
            qty = float(p.get("qty") or 0)
            # Alpaca reports a short as a negative qty, but not every payload does; the
            # side field is the authority when both are present.
            if (p.get("side") or "").lower() == "short" and qty > 0:
                qty = -qty
            return int(qty)
    return 0


def rebalance_order(target: int, current: int) -> Optional[Tuple[str, int]]:
    """(side, qty) to move the hedge from `current` to `target`, or None when already there.

    Whole shares, so a delta under one share is simply no order — that is the intended
    behaviour, not a rounding failure. A book whose beta nets out needs no hedge.
    """
    delta = target - current
    if delta == 0:
        return None
    return ("buy" if delta > 0 else "sell", abs(delta))


def submit_rebalance(client, cur, rows: List[Dict[str, Any]], spy_price: float, *,
                     agent_version: str, as_of, dry_run: bool = False) -> Dict[str, Any]:
    """Bring the netted hedge in line with the open book. Never gated by the halt latch.

    The latch stops NEW ENTRIES. Hedging is the opposite of taking risk: after a breach the
    book still needs its market exposure neutralised, and refusing to rebalance would leave
    it more directional than any cohort intended.
    """
    target = target_shares(rows, spy_price)
    current = current_shares(client.positions())
    order = rebalance_order(target, current)
    out = {"target": target, "current": current, "order": None}
    if order is None:
        return out

    side, qty = order
    # Keyed by (cohort, direction, size) so a retry within the same rebalance is idempotent
    # while a genuinely different target still gets its own order.
    coid = ledger.client_order_id(agent_version, as_of, f"{HEDGE_SYMBOL}{side}{qty}", "hedge")
    placed = client.submit(symbol=HEDGE_SYMBOL, qty=qty, side=side, order_type="market",
                           client_order_id=coid, dry_run=dry_run)
    if not dry_run:
        ledger.record_submission(cur, client_order_id=coid, ticker=HEDGE_SYMBOL, side=side,
                                 role="hedge", order_type="market", qty=qty, as_of=as_of,
                                 notional_target=qty * spy_price,
                                 broker_order_id=placed.get("id"),
                                 status=placed.get("status"))
    out["order"] = placed
    return out
