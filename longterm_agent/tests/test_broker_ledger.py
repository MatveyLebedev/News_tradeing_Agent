"""Ledger identity + drift detection. No database: the logic under test is pure."""
from datetime import date

import pytest

from longterm_agent.broker import ledger, reconcile


# --- client_order_id -----------------------------------------------------------------
def test_id_is_deterministic():
    """A retried scheduler must derive the same id, or idempotency is lost."""
    a = ledger.client_order_id("0.9.3", date(2026, 8, 10), "AAPL", "entry")
    b = ledger.client_order_id("0.9.3", date(2026, 8, 10), "AAPL", "entry")
    assert a == b


BASE = dict(agent_version="0.9.3", as_of=date(2026, 8, 10), ticker="AAPL", role="entry")


@pytest.mark.parametrize("field,value", [("agent_version", "0.9.4"),
                                         ("ticker", "MSFT"),
                                         ("role", "exit")])
def test_id_separates_every_dimension(field, value):
    assert ledger.client_order_id(**BASE) != ledger.client_order_id(**{**BASE, field: value})


def test_id_separates_cohorts():
    """Same ticker next week is a different trade, not a duplicate of this week's."""
    a = ledger.client_order_id("0.9.3", date(2026, 8, 10), "AAPL", "entry")
    b = ledger.client_order_id("0.9.3", date(2026, 8, 17), "AAPL", "entry")
    assert a != b


def test_id_is_greppable_in_the_broker_dashboard():
    cid = ledger.client_order_id("0.9.3", date(2026, 8, 10), "AAPL", "entry")
    assert cid.startswith("lt-entry-AAPL-") and len(cid) <= 128


def test_unknown_role_is_refused():
    with pytest.raises(ValueError, match="unknown role"):
        ledger.client_order_id("0.9.3", date(2026, 8, 10), "AAPL", "buy")


# --- drift ----------------------------------------------------------------------------
def test_no_drift_when_books_agree():
    assert ledger.drift(["AAPL", "MSFT"], ["MSFT", "AAPL"]) == (set(), set())


def test_decided_but_not_ordered_is_caught():
    """The book quietly shrinking — the 2026-07-26 failure mode."""
    missing, extra = ledger.drift(["AAPL", "MSFT"], ["AAPL"])
    assert missing == {"MSFT"} and extra == set()


def test_ordered_but_not_decided_is_caught():
    """A position nothing in the research ledger accounts for."""
    missing, extra = ledger.drift(["AAPL"], ["AAPL", "TSLA"])
    assert missing == set() and extra == {"TSLA"}


def test_drift_is_case_insensitive():
    assert ledger.drift(["aapl"], ["AAPL"]) == (set(), set())


# --- the message that reaches Telegram ------------------------------------------------
class _Cur:
    def __init__(self, traded, ordered):
        self._q = [[(t,) for t in traded], [(o,) for o in ordered]]
        self._i = -1

    def execute(self, *a, **k):
        self._i += 1

    def fetchall(self):
        return self._q[self._i]


def test_agreeing_books_produce_no_message():
    assert reconcile.check_cohort(_Cur(["AAPL"], ["AAPL"]), "0.9.3", date(2026, 8, 10)) is None


def test_drift_message_names_both_halves():
    msg = reconcile.check_cohort(_Cur(["AAPL", "MSFT"], ["AAPL", "TSLA"]),
                                 "0.9.3", date(2026, 8, 10))
    assert "MSFT" in msg and "TSLA" in msg
    assert "decided but NOT ordered" in msg and "ordered but NOT decided" in msg


# --- sync ------------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, answers):
        self.answers = answers

    def order_by_client_id(self, coid):
        return self.answers.get(coid)


class _SyncCur:
    def __init__(self, open_ids):
        self.open_ids, self.updates = open_ids, []

    def execute(self, sql, params=None):
        self._last = (sql, params)

    def fetchall(self):
        return [(i,) for i in self.open_ids]


def test_sync_counts_fills_and_open_orders(monkeypatch):
    cur = _SyncCur(["a", "b", "c"])
    monkeypatch.setattr(ledger, "record_fill", lambda *a, **k: None)
    client = _FakeClient({"a": {"filled_at": "2026-08-10T16:00:00Z", "status": "filled"},
                          "b": {"filled_at": None, "status": "new"}})
    out = reconcile.sync_open_orders(client, cur)
    assert out == {"checked": 3, "filled": 1, "still_open": 1, "gone": 1, "errors": 0}


def test_an_order_the_broker_never_heard_of_is_surfaced_not_swallowed(monkeypatch):
    """Never accepted vs cancelled out of band both need a human, so neither is retried."""
    cur = _SyncCur(["ghost"])
    monkeypatch.setattr(ledger, "record_fill", lambda *a, **k: pytest.fail("wrote a phantom"))
    out = reconcile.sync_open_orders(_FakeClient({}), cur)
    assert out["gone"] == 1 and out["filled"] == 0
