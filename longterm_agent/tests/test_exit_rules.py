"""Phase 5.5 tests: exit-rule simulator (pure — synthetic daily paths, no DB).

open_t1 fill convention: entry is the entry-bar OPEN passed explicitly; the entry bar's
own close already counts toward the stop (a gap can breach on day 0).
"""
from datetime import date, timedelta

from longterm_agent.exit_rules import simulate_exit
from longterm_agent.config import LongTermAgentConfig

D0 = date(2026, 6, 1)  # entry bar date
ENTRY = 100.0          # entry-bar open


def _path(closes, start=D0):
    """Consecutive daily closes starting at `start` (weekends don't matter to the rule)."""
    return [(start + timedelta(days=i), float(c)) for i, c in enumerate(closes)]


def test_stop_fires_on_breach_day_close():
    # LONG from open 100; day-2 close 89 (−11%) → stop at THAT close, not at −10% exactly
    p = _path([98, 95, 89, 120])
    s = simulate_exit(p, D0, ENTRY, "LONG", 28, stop_loss=0.10)
    assert s["reason"] == "stop"
    assert s["exit_px"] == 89.0 and s["days"] == 2
    # the later recovery to 120 is irrelevant — position is closed


def test_entry_day_close_can_stop():
    # open-entry position can breach on day 0: open 100, same-day close 88
    p = _path([88, 95])
    s = simulate_exit(p, D0, ENTRY, "LONG", 28, stop_loss=0.10)
    assert s["reason"] == "stop" and s["days"] == 0 and s["exit_px"] == 88.0


def test_gap_through_stop_exits_beyond_level():
    # SHORT from open 100; close gaps to 118 (+18% against the short) → exit at −18%
    p = _path([101, 118, 130])
    s = simulate_exit(p, D0, ENTRY, "SHORT", 28, stop_loss=0.10)
    assert s["reason"] == "stop" and s["exit_px"] == 118.0


def test_no_breach_exits_at_horizon():
    p = _path([103, 97, 102, 105])
    s = simulate_exit(p, D0, ENTRY, "LONG", 4, stop_loss=0.10)
    assert s["reason"] == "horizon"
    assert s["exit_date"] == D0 + timedelta(days=3) and s["exit_px"] == 105.0


def test_take_profit_fires_when_enabled_only():
    p = _path([110, 122, 90])
    on = simulate_exit(p, D0, ENTRY, "LONG", 28, stop_loss=0.10, take_profit=0.20)
    off = simulate_exit(p, D0, ENTRY, "LONG", 28, stop_loss=0.30, take_profit=0.0)
    assert on["reason"] == "take_profit" and on["exit_px"] == 122.0
    assert off["reason"] == "horizon"  # tp disabled, stop never hit


def test_stop_checked_before_tp_same_day():
    # LONG from 100: close 75 breaches the 10% stop long before any TP logic matters
    p = _path([75])
    s = simulate_exit(p, D0, ENTRY, "LONG", 28, stop_loss=0.10, take_profit=0.20)
    assert s["reason"] == "stop"


def test_short_direction_sign():
    # SHORT from open 100 → price falls to 85: position +15%, no stop; horizon exit
    p = _path([95, 85])
    s = simulate_exit(p, D0, ENTRY, "SHORT", 2, stop_loss=0.10)
    assert s["reason"] == "horizon" and s["exit_px"] == 85.0


def test_bars_before_entry_are_ignored():
    p = [(D0 - timedelta(days=2), 50.0)] + _path([98, 97])
    s = simulate_exit(p, D0, ENTRY, "LONG", 28, stop_loss=0.10)
    assert s["reason"] == "horizon" and s["entry_px"] == ENTRY


def test_no_bars_in_window_returns_none():
    p = _path([100, 101], start=D0 + timedelta(days=40))  # all closes AFTER the horizon
    assert simulate_exit(p, D0, ENTRY, "LONG", 28) is None
    assert simulate_exit([], D0, ENTRY, "LONG", 28) is None


def test_provenance_carries_exit_knobs():
    knobs = LongTermAgentConfig.provenance()["knobs"]
    assert knobs["stop_loss_pct"] == LongTermAgentConfig.STOP_LOSS_PCT
    assert knobs["take_profit_pct"] == LongTermAgentConfig.TAKE_PROFIT_PCT
