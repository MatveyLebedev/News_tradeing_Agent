"""Phase 10 tests: intraday-reaction bridge (pure formatter + maturity guard, no DB/graph)."""
from datetime import datetime

from longterm_agent.tools.intraday_tool import format_reactions, mature_move_ids

ROWS = [
    # (news_id, title, published, signals_json)
    (101, "NVDA export controls tightened", datetime(2026, 6, 1, 14),
     [{"ticker": "NVDA", "signal": "sell", "confidence": 0.8},
      {"ticker": "AMD", "signal": "sell", "confidence": 0.6}]),
    (102, "NVDA announces new AI chip", datetime(2026, 6, 3, 10),
     [{"ticker": "NVDA", "signal": "buy", "confidence": 0.75}]),
    (103, "Sector story without NVDA call", datetime(2026, 6, 4, 9),
     [{"ticker": "AVGO", "signal": "buy", "confidence": 0.7}]),
    (104, "NVDA supplier note", datetime(2026, 6, 5, 12),
     [{"ticker": "NVDA", "signal": "buy", "confidence": 0.6}]),
]


def test_format_reactions_hits_and_rate():
    moves = {101: -1.2, 102: 2.1, 104: -0.5}   # sell→down HIT, buy→up HIT, buy→down MISS
    out = format_reactions(ROWS, moves, "NVDA", 60)
    assert out["available"] is True
    assert out["n_events"] == 3                 # the AVGO-only row is excluded
    assert out["graded"] == 3 and out["hit_rate"] == 0.67
    ev = {e["title"][:4]: e for e in out["events"]}
    assert ev["NVDA"]["predicted"] in ("sell", "buy")
    hits = [e["hit"] for e in out["events"]]
    assert hits.count(True) == 2 and hits.count(False) == 1


def test_format_reactions_ungraded_when_no_moves():
    out = format_reactions(ROWS, {}, "NVDA", 60)
    assert out["available"] is True
    assert "hit_rate" not in out                # calls listed, nothing graded
    assert all(e["hit"] is None for e in out["events"])


def test_format_reactions_empty():
    assert format_reactions([], {}, "NVDA", 60)["available"] is False
    assert format_reactions(ROWS, {}, "KO", 60)["available"] is False  # no KO calls


# --- maturity guard (2026-07-19 PIT audit) ---------------------------------------------
# A `move_1d` only exists once the day AFTER the event has traded, so an event published
# just before as_of carries a reaction reaching past it. Those ids are never looked up.

def test_mature_move_ids_excludes_events_too_close_to_as_of():
    # cutoff = 06-04 09:00; 104 (06-05 12:00) is inside the window, 103 sits exactly on it
    as_of = datetime(2026, 6, 6, 9)
    assert mature_move_ids(ROWS, as_of) == [101, 102, 103]


def test_mature_move_ids_admits_fully_settled_events():
    as_of = datetime(2026, 6, 30)
    assert mature_move_ids(ROWS, as_of) == [101, 102, 103, 104]


def test_mature_move_ids_excludes_everything_when_nothing_has_settled():
    as_of = datetime(2026, 6, 2)             # cutoff 05-31: every row is newer
    assert mature_move_ids(ROWS, as_of) == []


def test_mature_move_ids_handles_tz_aware_publication_times():
    """agent_results.news_published is `timestamp WITH time zone` while as_of is naive ET —
    comparing them directly raises TypeError and took the whole tool down."""
    import pytz
    et = pytz.timezone("America/New_York")
    aware_rows = [(r[0], r[1], et.localize(r[2]), r[3]) for r in ROWS]
    assert mature_move_ids(aware_rows, datetime(2026, 6, 6, 9)) == [101, 102, 103]


def test_immature_events_are_reported_ungraded_not_dropped():
    """The event still shows as an intraday call; only its realized move is withheld."""
    as_of = datetime(2026, 6, 6, 9)
    moves = {nid: 1.5 for nid in mature_move_ids(ROWS, as_of)}
    out = format_reactions(ROWS, moves, "NVDA", 60)
    assert out["n_events"] == 3                        # 104 is still listed
    by_id = {e["title"]: e for e in out["events"]}
    assert by_id["NVDA supplier note"]["realized_move_1d_pct"] is None
    assert by_id["NVDA supplier note"]["hit"] is None
    assert out["graded"] == 2                          # and excluded from the hit rate
