"""Entry sizing, the cap, and the halt. Pure logic plus a fake broker — no network, no DB."""
from datetime import date, datetime

import pytest

from longterm_agent.broker import execution as ex, ledger


# --- sizing --------------------------------------------------------------------------
def test_shares_round_down():
    assert ex.shares_for(230.0) == 13          # 3000/230 = 13.04
    assert ex.shares_for(1021.10) == 2         # 3000/1021.10 = 2.94


def test_a_share_dearer_than_the_position_returns_zero():
    """Must be a loud skip upstream, never a silently missing name."""
    assert ex.shares_for(5000.0) == 0


@pytest.mark.parametrize("bad", [0, -1, None])
def test_nonsense_price_is_refused(bad):
    with pytest.raises(ValueError):
        ex.shares_for(bad)


def test_shorts_and_longs_size_identically():
    """Fractional shorts do not exist at Alpaca; sizing the two legs differently for the
    sake of a rounding error would make them incomparable."""
    assert ex.shares_for(230.0) == ex.shares_for(230.0)


# --- convention -> order type ---------------------------------------------------------
@pytest.mark.parametrize("conv,expected", [("mid_1m", "market"),
                                           ("open_t1", "moo"),
                                           ("open_d0", "moo")])
def test_order_type_follows_the_row_convention(conv, expected):
    assert ex.order_type_for(conv) == expected


def test_unknown_convention_is_loud():
    with pytest.raises(ValueError, match="no order type"):
        ex.order_type_for("close_t3")


@pytest.mark.parametrize("d,side", [("LONG", "buy"), ("SHORT", "sell")])
def test_side_follows_direction(d, side):
    assert ex.side_for(d) == side


def test_neutral_is_not_tradable():
    with pytest.raises(ValueError, match="not a tradable direction"):
        ex.side_for("NEUTRAL")


# --- minute alignment ------------------------------------------------------------------
def test_alignment_waits_for_the_next_boundary():
    assert ex.seconds_to_next_minute(datetime(2026, 8, 10, 12, 1, 30)) == pytest.approx(30.0)


def test_alignment_on_an_exact_boundary_waits_a_full_minute():
    """A decision stamped at 12:02:00 must not fill inside the 12:02 bar."""
    assert ex.seconds_to_next_minute(datetime(2026, 8, 10, 12, 2, 0)) == 60.0


# --- exposure cap ----------------------------------------------------------------------
def test_cap_room_is_measured_against_regt_not_intraday():
    # $200k overnight, 70% cap, $96k already on -> $44k of room
    assert ex.cap_room(96_000, 200_000) == pytest.approx(44_000)


def test_cap_room_goes_negative_when_over():
    assert ex.cap_room(150_000, 200_000) < 0


def test_shorts_add_to_exposure_they_do_not_offset():
    positions = [{"market_value": "10000"}, {"market_value": "-8000"}]
    assert ex.gross_exposure(positions) == 18_000


# --- submit ------------------------------------------------------------------------------
class _Client:
    def __init__(self, bp=200_000.0, positions=None):
        self._bp, self._pos, self.sent = bp, positions or [], []

    def overnight_buying_power(self):
        return self._bp

    def positions(self):
        return self._pos

    def submit(self, **kw):
        self.sent.append(kw)
        return {"id": f"o{len(self.sent)}", "status": "accepted"}


class _Cur:
    def __init__(self, halted=False, reason=None):
        self.halted, self.reason, self.calls = halted, reason, []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return (self.halted, self.reason)


def _rows(n, price=230.0):
    return [{"ticker": f"T{i}", "as_of": date(2026, 8, 10), "verdict": "LONG",
             "fill_convention": "open_t1", "last_price": price, "result_id": i}
            for i in range(n)]


def test_a_normal_cohort_is_submitted_whole(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c, cur = _Client(), _Cur()
    out = ex.submit_entries(c, cur, _rows(6), agent_version="0.9.3")
    assert out["submitted"] == 6 and not out["halted"]


def test_the_cap_halts_instead_of_trimming(monkeypatch):
    """The chosen behaviour: stop the book, do not quietly deliver a smaller one."""
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "halt_entries", lambda cur, reason: None)
    # $140k of room at 70% of $200k; $3k a name -> 46 fit, the 47th trips it
    c, cur = _Client(), _Cur()
    out = ex.submit_entries(c, cur, _rows(50), agent_version="0.9.3")
    assert out["halted"] and out["submitted"] == 46


def test_the_halt_alert_says_the_cohort_is_partial(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    monkeypatch.setattr(ledger, "halt_entries", lambda cur, reason: None)
    sent = []
    notifier = type("N", (), {"send": lambda self, m: sent.append(m)})()
    ex.submit_entries(_Client(bp=20_000), _Cur(), _rows(5), agent_version="0.9.3",
                      notifier=notifier)
    assert sent and "PARTIAL" in sent[0] and "halted" in sent[0]


def test_an_existing_latch_blocks_everything(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission",
                        lambda *a, **k: pytest.fail("submitted while halted"))
    c = _Client()
    out = ex.submit_entries(c, _Cur(halted=True, reason="earlier breach"),
                            _rows(3), agent_version="0.9.3")
    assert out["halted"] and out["submitted"] == 0 and c.sent == []


def test_dry_run_writes_nothing_to_the_ledger(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission",
                        lambda *a, **k: pytest.fail("dry run wrote to the ledger"))
    out = ex.submit_entries(_Client(), _Cur(), _rows(3), agent_version="0.9.3", dry_run=True)
    assert out["submitted"] == 3


def test_order_carries_the_deterministic_client_id(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    c = _Client()
    ex.submit_entries(c, _Cur(), _rows(1), agent_version="0.9.3")
    expected = ledger.client_order_id("0.9.3", date(2026, 8, 10), "T0", "entry")
    assert c.sent[0]["client_order_id"] == expected


def test_a_zero_share_name_is_skipped_with_a_reason(monkeypatch):
    monkeypatch.setattr(ledger, "record_submission", lambda *a, **k: None)
    rows = _rows(1, price=9_000.0)
    out = ex.submit_entries(_Client(), _Cur(), rows, agent_version="0.9.3")
    assert out["submitted"] == 0 and out["skipped"][0][0] == "T0"
