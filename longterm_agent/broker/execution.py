"""Decisions -> entry orders on the paper account.

Everything that decides *what* to send is a pure function here, so the arithmetic that sizes
a real position is unit-tested without a broker or a database. Only `submit_entries` touches
the outside world.

Sizing, and why these numbers:

  $3,000 per position, whole shares. The paper account holds $100k of equity, which is
  $200k of Reg-T overnight buying power (NOT the $400k `buying_power` field — that is the
  4x intraday figure and sizing a multi-week book against it overshoots by half).

  The number came down from $4,000 after a dry run against the real book: four overlapping
  cohorts held 25 positions, and the newest cohort was 8 names rather than the 5-6 the
  earlier cohorts averaged. At $4,000 a steady 8-a-week book is $128k — 91% of the cap,
  one name away from tripping it. At $3,000 the same book sits at 69% and the cap trips at
  11.7 names per cohort.

  The headroom is deliberately wide because the chosen breach behaviour is severe:
  everything stops until a human clears the latch. A cheap breach could afford a tight
  threshold; this one cannot.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

from longterm_agent.broker import ledger
from longterm_agent.exit_rules import FILL_CONVENTION, FILL_CONVENTION_D0, FILL_CONVENTION_MID

NOTIONAL_PER_POSITION = 3_000.0
EXPOSURE_CAP_FRACTION = 0.70


# --- pure ---------------------------------------------------------------------------------
def shares_for(price: float, notional: float = NOTIONAL_PER_POSITION) -> int:
    """Whole shares only.

    Alpaca supports fractional quantities for LONG positions but not for shorts, and this
    book is roughly half short. Sizing longs and shorts by different rules would make the
    two legs incomparable for the sake of a rounding error, so both round down.

    Returns 0 when one share costs more than the position — the caller must treat that as a
    loud skip, never a silent one: at $4,000 nothing in the current universe rounds to zero,
    so a zero here means the price moved somewhere the sizing did not anticipate.
    """
    if price is None or price <= 0:
        raise ValueError(f"price must be > 0, got {price!r}")
    return int(math.floor(notional / price))


def order_type_for(fill_convention: str) -> str:
    """The broker order that reproduces the row's own fill convention.

    Picked per row rather than from a config flag, mirroring `exit_rules.resolve_entry`, so
    this layer survives the schedule migration to Monday midday without being rewritten and
    keeps pricing today's Sunday book correctly in the meantime.

      open_t1 / open_d0 -> the next session's open      -> MOO
      mid_1m            -> the first minute after the decision -> market, sent on the
                           minute boundary (the caller aligns; see seconds_to_next_minute)
    """
    if fill_convention == FILL_CONVENTION_MID:
        return "market"
    if fill_convention in (FILL_CONVENTION, FILL_CONVENTION_D0):
        return "moo"
    raise ValueError(f"no order type for fill convention {fill_convention!r}")


def side_for(direction: str) -> str:
    d = (direction or "").upper()
    if d == "LONG":
        return "buy"
    if d == "SHORT":
        return "sell"
    raise ValueError(f"not a tradable direction: {direction!r}")


def seconds_to_next_minute(now) -> float:
    """Delay before a `mid_1m` order may be sent.

    The convention fills at the OPEN of the first minute bar starting strictly after the
    decision. Firing the instant the row persists would take a price from inside the current
    minute — earlier than the convention allows — and the gap between broker fill and graded
    `entry_px` would then mix timing error with execution cost and measure neither.
    """
    return (60 - now.second - now.microsecond / 1e6) % 60 or 60.0


def gross_exposure(positions: List[Dict[str, Any]]) -> float:
    """Absolute market value across the book — shorts count as exposure, not as offset."""
    return sum(abs(float(p.get("market_value") or 0.0)) for p in positions)


def cap_room(gross: float, overnight_bp: float,
             cap_fraction: float = EXPOSURE_CAP_FRACTION) -> float:
    """Notional still available under the cap. Negative means already over."""
    return cap_fraction * overnight_bp - gross


# --- the one impure entry point ------------------------------------------------------------
def submit_entries(client, cur, rows: List[Dict[str, Any]], *, agent_version: str,
                   notifier=None, dry_run: bool = False,
                   notional: float = NOTIONAL_PER_POSITION) -> Dict[str, Any]:
    """Submit one entry per decided row. Idempotent; halts the book rather than trimming it.

    `rows` carry: ticker, as_of, verdict, fill_convention, last_price, result_id.
    """
    out = {"submitted": 0, "skipped": [], "halted": False, "orders": []}

    halted, reason = ledger.is_halted(cur)
    if halted:
        out["halted"] = True
        out["reason"] = reason
        return out

    overnight_bp = client.overnight_buying_power()
    gross = gross_exposure(client.positions())

    for r in rows:
        room = cap_room(gross, overnight_bp)
        if room < notional:
            msg = (f"🛑 <b>entries halted</b> — exposure cap reached at {r['ticker']}. "
                   f"gross ${gross:,.0f} of ${EXPOSURE_CAP_FRACTION:.0%} × "
                   f"${overnight_bp:,.0f}. Cohort {r['as_of']} is PARTIAL and not "
                   f"comparable to the ledger. Clear the latch by hand to resume.")
            ledger.halt_entries(cur, msg)
            if notifier is not None:
                notifier.send(msg)
            out["halted"] = True
            out["reason"] = msg
            break

        qty = shares_for(r["last_price"], notional)
        if qty == 0:
            out["skipped"].append((r["ticker"], "one share costs more than the position"))
            continue

        coid = ledger.client_order_id(agent_version, r["as_of"], r["ticker"], "entry")
        order = client.submit(symbol=r["ticker"], qty=qty, side=side_for(r["verdict"]),
                              order_type=order_type_for(r["fill_convention"]),
                              client_order_id=coid, dry_run=dry_run)
        if not dry_run:
            ledger.record_submission(
                cur, client_order_id=coid, ticker=r["ticker"], side=side_for(r["verdict"]),
                role="entry", order_type=order_type_for(r["fill_convention"]), qty=qty,
                as_of=r["as_of"], result_id=r.get("result_id"),
                notional_target=notional, broker_order_id=order.get("id"),
                status=order.get("status"))
        gross += qty * r["last_price"]
        out["submitted"] += 1
        out["orders"].append(order)

    return out
