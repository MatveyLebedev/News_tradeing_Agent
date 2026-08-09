"""Closing positions: the horizon, and the stop.

Two triggers, two order types, for reasons the grader dictates:

  * HORIZON — the convention exits "at the close on or before entry_date + horizon"
    (docs/STRATEGY_MONDAY_MIDDAY.md). The only broker order that prices at the official
    close is market-on-close, and it has to be in before roughly 15:50 ET. A plain market
    order at 15:59 would fill at a different price than the one the grader scores.

  * STOP — `grade_results.scan_open_breaches` already detects a breach and stamps
    `exit_alert_at`. A stop is a get-out-now event, so it closes at market immediately and
    is not held for the close.

Neither is gated by the halt latch: the latch stops NEW risk, and refusing to close would
make a breach permanent — see broker/ledger.py.
"""
from datetime import date, time, timedelta
from typing import Any, Dict, List, Optional

from longterm_agent.broker import ledger

HORIZON_DAYS = 28          # mirrors grade_results.HORIZON_DAYS — the 4w checkpoint
MOC_CUTOFF = time(15, 50)  # Alpaca stops accepting market-on-close around here


# --- pure -----------------------------------------------------------------------------
def exit_due(entry_date: date, today: date, horizon_days: int = HORIZON_DAYS) -> bool:
    """Has this position reached its horizon?

    KNOWN DIVERGENCE, recorded rather than engineered around: the grader exits at the last
    close on or before `entry_date + horizon`, which it can resolve after the fact from the
    price table. The broker has to act beforehand, so when the target lands on a weekend or
    holiday this fires on the next session instead — the position is closed one session
    later than the graded exit. Bounded, one-directional, and visible in the ledger as the
    gap between `filled_at` and the graded exit date.
    """
    return today >= entry_date + timedelta(days=horizon_days)


def can_submit_moc(now_time: time, cutoff: time = MOC_CUTOFF) -> bool:
    """MOC is only accepted before the cutoff; after it, the close is already unreachable."""
    return now_time < cutoff


def closing_side(entry_side: str) -> str:
    if entry_side == "buy":
        return "sell"
    if entry_side == "sell":
        return "buy"
    raise ValueError(f"not an entry side: {entry_side!r}")


# --- reads ------------------------------------------------------------------------------
def closable(cur, agent_version: str, today: date) -> List[Dict[str, Any]]:
    """Filled entries with no exit yet, annotated with why they should close (or None).

    The horizon anchors on `COALESCE(r.entry_date, broker fill date)`, and the fallback is
    not cosmetic: `entry_date` is stamped by the GRADER, which only runs a row at maturity.
    Measured on the live book — 25 open positions, `entry_date` populated on exactly zero of
    them. Anchoring on it alone would mean the horizon exit never fires until after the
    grader has already closed the trade on paper.

    The two agree when both exist: an `open_t1` entry fills at the next session's open,
    which is the date the grader records, and a `mid_1m` entry fills the same day it was
    decided. So the fallback is the same date, available weeks earlier.
    """
    cur.execute("""
        SELECT o.client_order_id, o.ticker, o.side, o.filled_qty, o.result_id,
               COALESCE(r.entry_date, (o.filled_at AT TIME ZONE 'America/New_York')::date),
               r.as_of, r.exit_alert_at
          FROM paper_orders o
          JOIN longterm_agent_results r ON r.id = o.result_id
         WHERE o.role = 'entry' AND o.filled_at IS NOT NULL
           AND r.agent_version = %s
           AND NOT EXISTS (SELECT 1 FROM paper_orders x
                            WHERE x.result_id = o.result_id AND x.role IN ('exit', 'stop'))
    """, [agent_version])
    out = []
    for coid, tk, side, qty, rid, entry_date, as_of, stopped_at in cur.fetchall():
        if stopped_at is not None:
            reason = "stop"
        elif entry_date is not None and exit_due(entry_date, today):
            reason = "exit"
        else:
            continue
        out.append({"entry_client_order_id": coid, "ticker": tk, "entry_side": side,
                    "qty": float(qty or 0), "result_id": rid, "as_of": as_of,
                    "entry_date": entry_date, "reason": reason})
    return out


# --- the write ----------------------------------------------------------------------------
def submit_exits(client, cur, rows: List[Dict[str, Any]], *, agent_version: str,
                 now_time: time, dry_run: bool = False) -> Dict[str, Any]:
    """Close what is due. Horizon exits go MOC; stops go market, now."""
    out = {"exits": 0, "stops": 0, "deferred": [], "orders": []}
    for r in rows:
        if r["qty"] <= 0:
            continue                     # entry never actually filled a share
        role = r["reason"]
        if role == "exit":
            if not can_submit_moc(now_time):
                # Past the MOC window: leave it for tomorrow rather than close at a price
                # the grader does not use. Deferring is visible; a wrong price is not.
                out["deferred"].append(r["ticker"])
                continue
            order_type = "moc"
        else:
            order_type = "market"

        coid = ledger.client_order_id(agent_version, r["as_of"], r["ticker"], role)
        placed = client.submit(symbol=r["ticker"], qty=r["qty"],
                               side=closing_side(r["entry_side"]),
                               order_type=order_type, client_order_id=coid, dry_run=dry_run)
        if not dry_run:
            ledger.record_submission(cur, client_order_id=coid, ticker=r["ticker"],
                                     side=closing_side(r["entry_side"]), role=role,
                                     order_type=order_type, qty=r["qty"], as_of=r["as_of"],
                                     result_id=r["result_id"],
                                     broker_order_id=placed.get("id"),
                                     status=placed.get("status"))
        out["stops" if role == "stop" else "exits"] += 1
        out["orders"].append(placed)
    return out
