"""Netted SPY hedge: exposure arithmetic, rounding, and when NOT to trade."""
from datetime import date

import pytest

from longterm_agent.broker import hedge, ledger


def row(verdict, beta, notional=4000.0):
    return {"verdict": verdict, "beta": beta, "notional": notional}


# --- exposure -------------------------------------------------------------------------
def test_long_book_has_positive_beta_exposure():
    assert hedge.signed_beta_notional([row("LONG", 1.0), row("LONG", 0.5)]) == 6000.0


def test_shorts_subtract():
    assert hedge.signed_beta_notional([row("LONG", 1.0), row("SHORT", 1.0)]) == 0.0


def test_a_balanced_book_needs_no_hedge():
    """The usual case for a roughly direction-balanced book — zero is an answer."""
    rows = [row("LONG", 0.8), row("SHORT", 0.8)]
    assert hedge.target_shares(rows, 700.0) == 0
    assert hedge.rebalance_order(0, 0) is None


def test_missing_beta_counts_as_one_not_dropped():
    """BENCHMARK_CONVENTION keeps beta-less trades at 1.0 so the denominator never moves
    silently; the hedge must neutralise the same book that is being scored."""
    assert hedge.signed_beta_notional([row("LONG", None)]) == 4000.0


# --- target ----------------------------------------------------------------------------
def test_long_exposure_is_hedged_by_a_short():
    # 10 x $4,000 at beta 0.48 = $19,200 of exposure; SPY at $700 -> 27 shares short
    rows = [row("LONG", 0.48) for _ in range(10)]
    assert hedge.target_shares(rows, 700.0) == -27


def test_short_book_is_hedged_by_a_long():
    rows = [row("SHORT", 1.0) for _ in range(2)]
    assert hedge.target_shares(rows, 700.0) > 0


def test_rounding_is_toward_zero():
    """Under-hedging by a fraction beats over-hedging into exposure nobody asked for."""
    rows = [row("LONG", 1.0, notional=699.0)]           # 0.998 shares
    assert hedge.target_shares(rows, 700.0) == 0


@pytest.mark.parametrize("bad", [0, -1, None])
def test_nonsense_spy_price_is_refused(bad):
    with pytest.raises(ValueError):
        hedge.target_shares([row("LONG", 1.0)], bad)


# --- current position reading ----------------------------------------------------------
def test_no_spy_position_reads_as_zero():
    assert hedge.current_shares([{"symbol": "AAPL", "qty": "10"}]) == 0


def test_short_position_reads_negative():
    assert hedge.current_shares([{"symbol": "SPY", "qty": "-27", "side": "short"}]) == -27


def test_side_field_wins_over_an_unsigned_qty():
    """Not every payload signs the quantity; `side` is the authority when both are present."""
    assert hedge.current_shares([{"symbol": "SPY", "qty": "27", "side": "short"}]) == -27


# --- rebalance --------------------------------------------------------------------------
def test_already_on_target_places_nothing():
    assert hedge.rebalance_order(-27, -27) is None


def test_growing_a_short_sells_more():
    assert hedge.rebalance_order(-30, -27) == ("sell", 3)


def test_shrinking_a_short_buys_back():
    assert hedge.rebalance_order(-20, -27) == ("buy", 7)


# --- submission ---------------------------------------------------------------------------
class _Client:
    def __init__(self, positions=None):
        self._pos, self.sent = positions or [], []

    def positions(self):
        return self._pos

    def submit(self, **kw):
        self.sent.append(kw)
        return {"id": "h1", "status": "accepted"}


class _Cur:
    def execute(self, *a, **k):
        pass


def test_rebalance_submits_one_order_for_the_whole_book(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c = _Client()
    rows = [row("LONG", 0.48) for _ in range(10)]
    out = hedge.submit_rebalance(c, _Cur(), rows, 700.0, agent_version="0.9.3",
                                 as_of=date(2026, 8, 10))
    assert len(c.sent) == 1
    assert c.sent[0]["symbol"] == "SPY" and c.sent[0]["side"] == "sell"
    assert out["target"] == -27


def test_a_flat_book_submits_nothing(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission",
                        lambda *a, **k: pytest.fail("hedged a flat book"))
    c = _Client()
    out = hedge.submit_rebalance(c, _Cur(), [row("LONG", 1.0), row("SHORT", 1.0)], 700.0,
                                 agent_version="0.9.3", as_of=date(2026, 8, 10))
    assert out["order"] is None and c.sent == []


def test_dry_run_places_no_ledger_row(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission",
                        lambda *a, **k: pytest.fail("dry run wrote to the ledger"))
    c = _Client()
    hedge.submit_rebalance(c, _Cur(), [row("LONG", 1.0) for _ in range(10)], 700.0,
                           agent_version="0.9.3", as_of=date(2026, 8, 10), dry_run=True)
    assert len(c.sent) == 1


def test_hedge_ignores_the_halt_latch(monkeypatch):
    """The latch stops new risk. Refusing to hedge after a breach would leave the book
    MORE directional than any cohort intended."""
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "is_halted",
                        lambda cur: pytest.fail("hedge consulted the entry latch"))
    c = _Client()
    hedge.submit_rebalance(c, _Cur(), [row("LONG", 1.0) for _ in range(10)], 700.0,
                           agent_version="0.9.3", as_of=date(2026, 8, 10))
    assert len(c.sent) == 1
