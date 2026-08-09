"""Horizon exits (MOC) and stop exits (market)."""
from datetime import date, time

import pytest

from longterm_agent.broker import exits, ledger


# --- horizon --------------------------------------------------------------------------
def test_not_due_before_the_horizon():
    assert not exits.exit_due(date(2026, 8, 10), date(2026, 9, 6))


def test_due_on_the_horizon_day():
    assert exits.exit_due(date(2026, 8, 10), date(2026, 9, 7))     # +28


def test_still_due_after_it():
    """A missed day must not strand the position forever."""
    assert exits.exit_due(date(2026, 8, 10), date(2026, 9, 20))


# --- MOC window -------------------------------------------------------------------------
def test_moc_accepted_in_the_morning():
    assert exits.can_submit_moc(time(7, 0))


def test_moc_refused_after_the_cutoff():
    assert not exits.can_submit_moc(time(15, 55))


def test_a_late_horizon_exit_defers_rather_than_mispricing(monkeypatch):
    """Closing at market at 15:59 would fill at a price the grader never uses."""
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    rows = [_row(reason="exit")]
    out = exits.submit_exits(_Client(), _Cur(), rows, agent_version="0.2.0",
                             now_time=time(15, 55))
    assert out["exits"] == 0 and out["deferred"] == ["AAPL"]


# --- sides -------------------------------------------------------------------------------
@pytest.mark.parametrize("entry,close", [("buy", "sell"), ("sell", "buy")])
def test_closing_side_is_the_opposite(entry, close):
    assert exits.closing_side(entry) == close


def test_unknown_side_is_loud():
    with pytest.raises(ValueError):
        exits.closing_side("short")


# --- submission ---------------------------------------------------------------------------
class _Client:
    def __init__(self):
        self.sent = []

    def submit(self, **kw):
        self.sent.append(kw)
        return {"id": f"x{len(self.sent)}", "status": "accepted"}


class _Cur:
    def execute(self, *a, **k):
        pass


def _row(reason="exit", qty=10.0, side="buy", ticker="AAPL"):
    return {"entry_client_order_id": "c1", "ticker": ticker, "entry_side": side,
            "qty": qty, "result_id": 1, "as_of": date(2026, 8, 10),
            "entry_date": date(2026, 8, 11), "reason": reason}


def test_horizon_exit_goes_market_on_close(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c = _Client()
    out = exits.submit_exits(c, _Cur(), [_row("exit")], agent_version="0.2.0",
                             now_time=time(7, 0))
    assert out["exits"] == 1 and c.sent[0]["order_type"] == "moc"
    assert c.sent[0]["side"] == "sell"


def test_stop_goes_market_immediately_even_past_the_moc_window(monkeypatch):
    """A stop is get-out-now; holding it for the close would be the opposite of a stop."""
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c = _Client()
    out = exits.submit_exits(c, _Cur(), [_row("stop")], agent_version="0.2.0",
                             now_time=time(15, 58))
    assert out["stops"] == 1 and c.sent[0]["order_type"] == "market"


def test_a_short_is_closed_by_buying_back(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c = _Client()
    exits.submit_exits(c, _Cur(), [_row("exit", side="sell")], agent_version="0.2.0",
                       now_time=time(7, 0))
    assert c.sent[0]["side"] == "buy"


def test_an_unfilled_entry_is_not_closed(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission",
                        lambda *a, **k: pytest.fail("closed a position of zero shares"))
    c = _Client()
    out = exits.submit_exits(c, _Cur(), [_row("exit", qty=0.0)], agent_version="0.2.0",
                             now_time=time(7, 0))
    assert out["exits"] == 0 and c.sent == []


def test_exit_and_stop_get_different_client_order_ids(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c = _Client()
    exits.submit_exits(c, _Cur(), [_row("exit", ticker="AAPL")], agent_version="0.2.0",
                       now_time=time(7, 0))
    exits.submit_exits(c, _Cur(), [_row("stop", ticker="AAPL")], agent_version="0.2.0",
                       now_time=time(7, 0))
    assert c.sent[0]["client_order_id"] != c.sent[1]["client_order_id"]


def test_dry_run_writes_no_ledger_row(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission",
                        lambda *a, **k: pytest.fail("dry run wrote to the ledger"))
    out = exits.submit_exits(_Client(), _Cur(), [_row("exit")], agent_version="0.2.0",
                             now_time=time(7, 0), dry_run=True)
    assert out["exits"] == 1
