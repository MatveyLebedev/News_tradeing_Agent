"""The execution ledger: what the broker was told to do, and what it actually did.

Deliberately a SEPARATE table from `longterm_agent_results`. The decision ledger is the
research record; this is an operational one. Keeping them apart means a broker outage, a
duplicate submission or a botched reconciliation can never corrupt a graded row — the two
are joined for display only.

Schema is ensured in code (`ensure_schema`), matching how `persistence._ensure_results_table`
and `grade_results._ensure_exit_columns` already work here, so there is no separate migration
step to forget on deploy.
"""
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

DDL = """
CREATE TABLE IF NOT EXISTS paper_orders (
    id                SERIAL PRIMARY KEY,
    result_id         INTEGER REFERENCES longterm_agent_results(id),
    client_order_id   TEXT NOT NULL UNIQUE,
    ticker            TEXT NOT NULL,
    side              TEXT NOT NULL,
    role              TEXT NOT NULL,
    order_type        TEXT NOT NULL,
    qty               NUMERIC NOT NULL,
    notional_target   NUMERIC,
    as_of             DATE,
    submitted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    broker_order_id   TEXT,
    status            TEXT,
    filled_qty        NUMERIC,
    filled_avg_price  NUMERIC,
    filled_at         TIMESTAMPTZ
);
COMMENT ON TABLE paper_orders IS
  'Paper-broker execution ledger. Never an input to grading.';
CREATE INDEX IF NOT EXISTS idx_paper_orders_as_of ON paper_orders (as_of);
CREATE INDEX IF NOT EXISTS idx_paper_orders_open
    ON paper_orders (status) WHERE filled_at IS NULL;

-- The halt latch. A breached exposure cap stops NEW ENTRIES and stays stopped until a
-- human clears it: if the cap tripped once the book is already at its limit, and resuming
-- next Monday would simply trip again and produce a run of half-cohorts. Exits, stops and
-- hedge rebalances are deliberately NOT gated by this — they reduce exposure, and halting
-- them would make a temporary breach permanent.
CREATE TABLE IF NOT EXISTS broker_state (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    entries_halted  BOOLEAN NOT NULL DEFAULT FALSE,
    halted_at       TIMESTAMPTZ,
    halted_reason   TEXT
);
INSERT INTO broker_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
"""

# Roles a row can play. `hedge` carries no result_id — it belongs to the book, not a trade.
ROLES = ("entry", "exit", "stop", "hedge")
OPEN_STATUSES = ("new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding")


def ensure_schema(cur) -> None:
    cur.execute(DDL)


def client_order_id(agent_version: str, as_of, ticker: str, role: str) -> str:
    """Deterministic id — the same (version, cohort, ticker, role) always yields the same one.

    This is the whole idempotency story: Alpaca rejects a repeat, `AlpacaPaper.submit`
    treats that rejection as success, and a re-run of the scheduler therefore opens nothing
    twice. Derived rather than random for exactly that reason — a random id would make a
    retry indistinguishable from a new order.

    Hashed because Alpaca caps the field and a raw ticker+date+role is both longer than it
    needs to be and awkward to eyeball; the prefix keeps it greppable in their dashboard.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; known: {ROLES}")
    raw = f"{agent_version}|{as_of}|{ticker.upper()}|{role}"
    return f"lt-{role}-{ticker.upper()}-{hashlib.sha1(raw.encode()).hexdigest()[:12]}"


def record_submission(cur, *, client_order_id: str, ticker: str, side: str, role: str,
                      order_type: str, qty: float, as_of, result_id: Optional[int] = None,
                      notional_target: Optional[float] = None,
                      broker_order_id: Optional[str] = None,
                      status: Optional[str] = None) -> None:
    """Write (or refresh) the row for one submitted order.

    ON CONFLICT so a retry that hits Alpaca's duplicate rejection still records the broker
    id it got back, instead of raising and leaving the ledger blind to an order that exists.
    """
    cur.execute("""
        INSERT INTO paper_orders (result_id, client_order_id, ticker, side, role,
                                  order_type, qty, notional_target, as_of,
                                  broker_order_id, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (client_order_id) DO UPDATE
            SET broker_order_id = COALESCE(EXCLUDED.broker_order_id,
                                           paper_orders.broker_order_id),
                status          = COALESCE(EXCLUDED.status, paper_orders.status)
    """, [result_id, client_order_id, ticker.upper(), side, role, order_type, qty,
          notional_target, as_of, broker_order_id, status])


def record_fill(cur, client_order_id: str, order: Dict[str, Any]) -> None:
    """Copy the broker's own view of an order onto our row. Broker is authoritative here."""
    cur.execute("""
        UPDATE paper_orders
           SET status = %s, filled_qty = %s, filled_avg_price = %s, filled_at = %s,
               broker_order_id = COALESCE(%s, broker_order_id)
         WHERE client_order_id = %s
    """, [order.get("status"),
          order.get("filled_qty") or None,
          order.get("filled_avg_price") or None,
          order.get("filled_at") or None,
          order.get("id"), client_order_id])


def open_order_ids(cur) -> List[str]:
    """Orders we have submitted and not yet seen fill or die — the sync work list."""
    cur.execute("""SELECT client_order_id FROM paper_orders
                    WHERE filled_at IS NULL
                      AND (status IS NULL OR status = ANY(%s))""", [list(OPEN_STATUSES)])
    return [r[0] for r in cur.fetchall()]


# --- drift: the check that makes a silent divergence loud -------------------------------
def drift(traded: Iterable[str], ordered: Iterable[str]) -> Tuple[Set[str], Set[str]]:
    """(decided-but-not-ordered, ordered-but-not-decided) for one cohort.

    Pure, so the check itself is unit-tested without a database. Both halves matter and for
    different reasons: the first is the book quietly shrinking — the failure mode that cost
    a full cohort on 2026-07-26 when decisions went unpersisted and read as "no opinion".
    The second is a position nothing accounts for.
    """
    t, o = {x.upper() for x in traded}, {x.upper() for x in ordered}
    return t - o, o - t


# --- halt latch -------------------------------------------------------------------------
def is_halted(cur) -> Tuple[bool, Optional[str]]:
    cur.execute("SELECT entries_halted, halted_reason FROM broker_state WHERE id = 1")
    row = cur.fetchone()
    return (bool(row[0]), row[1]) if row else (False, None)


def halt_entries(cur, reason: str) -> None:
    cur.execute("""UPDATE broker_state
                      SET entries_halted = TRUE, halted_at = NOW(), halted_reason = %s
                    WHERE id = 1""", [reason])


def clear_halt(cur) -> None:
    """Manual reset. Documented in docs/ so the latch is not a one-way door:

        UPDATE broker_state SET entries_halted = FALSE, halted_reason = NULL WHERE id = 1;
    """
    cur.execute("""UPDATE broker_state
                      SET entries_halted = FALSE, halted_at = NULL, halted_reason = NULL
                    WHERE id = 1""")


def entry_candidates(cur, agent_version: str, as_of, notional: float,
                     start_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Decided trades of one cohort that still need an entry order.

    Excludes anything already ordered, so a re-run adds nothing — the deterministic
    client_order_id makes a duplicate harmless at the broker, but not asking is cheaper.
    `start_date` is the cutoff: older cohorts keep showing the grader's prices and are never
    opened retroactively.
    """
    if start_date and str(as_of) < str(start_date):
        return []
    cur.execute("""
        SELECT r.id, r.ticker, r.as_of, r.verdict, r.fill_convention, r.beta,
               (SELECT close FROM stock_prices_daily p
                 WHERE p.ticker = r.ticker ORDER BY date DESC LIMIT 1)
          FROM longterm_agent_results r
         WHERE r.agent_version = %s AND r.as_of = %s AND r.traded
           AND NOT EXISTS (SELECT 1 FROM paper_orders o
                            WHERE o.result_id = r.id AND o.role = 'entry')
         ORDER BY r.ticker
    """, [agent_version, as_of])
    out = []
    for rid, tk, aof, verdict, conv, beta, px in cur.fetchall():
        if px is None:
            continue          # unpriced: the caller reports it, never guesses a size
        out.append({"result_id": rid, "ticker": tk, "as_of": aof, "verdict": verdict,
                    "fill_convention": conv or "open_t1", "beta": beta,
                    "last_price": float(px), "notional": notional})
    return out


def broker_book(cur, agent_version: str, notional: float) -> List[Dict[str, Any]]:
    """The positions the BROKER actually holds, as rows the hedge can price.

    Deliberately not "every open row in longterm_agent_results": only trades from the
    cutoff onward are sent to the broker, so for a while the two books differ by design.
    Hedging the research book would neutralise exposure the paper account does not carry.
    """
    cur.execute("""
        SELECT r.ticker, r.verdict, r.beta
          FROM paper_orders o
          JOIN longterm_agent_results r ON r.id = o.result_id
         WHERE o.role = 'entry'
           AND r.agent_version = %s
           AND r.fwd_return_4w IS NULL
           AND NOT EXISTS (SELECT 1 FROM paper_orders x
                            WHERE x.result_id = o.result_id AND x.role IN ('exit', 'stop'))
    """, [agent_version])
    return [{"ticker": t, "verdict": v, "beta": b, "notional": notional}
            for t, v, b in cur.fetchall()]


def cohort_drift(cur, agent_version: str, as_of) -> Tuple[Set[str], Set[str]]:
    cur.execute("""SELECT ticker FROM longterm_agent_results
                    WHERE agent_version=%s AND as_of=%s AND traded""",
                [agent_version, as_of])
    traded = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT ticker FROM paper_orders
                    WHERE as_of=%s AND role='entry'""", [as_of])
    return drift(traded, [r[0] for r in cur.fetchall()])
