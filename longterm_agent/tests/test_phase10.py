"""Phase 10 tests: intraday-reaction bridge (pure formatter, no DB/graph)."""
from datetime import datetime

from longterm_agent.tools.intraday_tool import format_reactions

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
