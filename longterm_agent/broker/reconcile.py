"""Sync the ledger with the broker, and shout when the two books disagree.

Runs from the daily job. Two jobs, in order:

  1. `sync_open_orders` — ask the broker what happened to everything we submitted and have
     not yet seen settle. The broker is authoritative about its own orders.
  2. `check_cohort` — compare the decisions that SHOULD have been traded against the orders
     that exist. This is the part that earns its keep: every failure this project has
     actually suffered on the write path was silent. A dead writer printed "Inserted 0
     records" nightly for months; a lost cohort read as "the agent had no opinion". A
     divergence here must reach Telegram, not a log line.
"""
from typing import Any, Dict, List, Optional, Tuple

from longterm_agent.broker import ledger
from longterm_agent.broker.alpaca_client import AlpacaPaper, BrokerError


def sync_open_orders(client: AlpacaPaper, cur) -> Dict[str, int]:
    """Refresh every unsettled order from the broker. Returns counts by outcome."""
    out = {"checked": 0, "filled": 0, "still_open": 0, "gone": 0, "errors": 0}
    for coid in ledger.open_order_ids(cur):
        out["checked"] += 1
        try:
            order = client.order_by_client_id(coid)
        except BrokerError:
            out["errors"] += 1
            continue
        if order is None:
            # Submitted by us, unknown to the broker. Not a fill and not an open order —
            # surfaced rather than reconciled away, because the two cases (never accepted /
            # cancelled out of band) need a human, not a retry.
            out["gone"] += 1
            continue
        ledger.record_fill(cur, coid, order)
        if order.get("filled_at"):
            out["filled"] += 1
        else:
            out["still_open"] += 1
    return out


def check_cohort(cur, agent_version: str, as_of) -> Optional[str]:
    """None when the books agree, else a message ready to send to Telegram."""
    missing, extra = ledger.cohort_drift(cur, agent_version, as_of)
    if not (missing or extra):
        return None
    parts = [f"⚠️ <b>broker/book drift</b> for {as_of}"]
    if missing:
        parts.append(f"decided but NOT ordered ({len(missing)}): "
                     f"{', '.join(sorted(missing))}")
    if extra:
        parts.append(f"ordered but NOT decided ({len(extra)}): "
                     f"{', '.join(sorted(extra))}")
    return "\n".join(parts)


def run(cur, notifier=None, agent_version: str = None, as_of=None,
        client: AlpacaPaper = None) -> Dict[str, Any]:
    """One reconciliation pass. Safe to call when nothing has been submitted yet."""
    client = client or AlpacaPaper()
    ledger.ensure_schema(cur)
    result: Dict[str, Any] = {"sync": sync_open_orders(client, cur)}
    if agent_version and as_of is not None:
        msg = check_cohort(cur, agent_version, as_of)
        result["drift"] = msg
        if msg and notifier is not None:
            notifier.send(msg)
    return result
