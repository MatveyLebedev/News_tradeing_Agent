"""
Intraday ↔ long-term memory bridge (Modification Plan Phase 10).

The intraday agent (news_agent) has been calling short-horizon direction on significant
events for months (`agent_results`), and the graph carries each event's REALIZED
short-horizon reaction (`AFFECTS.move_1d`). This read-only, as-of-windowed join lets the
long-term deep analysis see how the market ACTUALLY treated the window's events — a
reality check on the narrative ("the news says X is bad, but every X event was bought").

Advisory only, like every deep-analysis tool. Pure formatter split out for tests.

MATURITY: a realized `move_1d` only exists once the day AFTER the event has traded, so
an event published shortly before `as_of` carries a reaction that extends PAST it. Such
events are excluded from the move lookup entirely (they simply come back ungraded) —
the guard sits at the read, where `as_of` is known, so no downstream formatter can
forget it.
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import Json

from .pit import as_moment

# Calendar days an event must predate `as_of` before its 1-day reaction counts as
# realized. Two spans a weekend partially and is deliberately conservative: the cost of
# over-masking is a missing advisory data point, the cost of under-masking is lookahead.
MOVE_MATURITY_DAYS = 2


def mature_move_ids(rows: List[tuple], as_of) -> List[int]:
    """news_ids whose 1-day reaction had fully played out by `as_of`.

    Pure so the leakage guard itself is unit-tested: `rows` are the agent_results tuples
    (news_id, title, published, signals_json). An event published inside the maturity
    window is excluded outright — its move_1d would extend past the decision.

    Both sides go through `as_moment` because the two clocks differ in the DB:
    `agent_results.news_published` is `timestamp WITH time zone` (psycopg2 hands back an
    aware datetime) while `as_of` is naive ET — comparing them directly raises TypeError.
    """
    cutoff = as_moment(as_of) - timedelta(days=MOVE_MATURITY_DAYS)
    out = []
    for r in rows:
        pub = as_moment(r[2]) if len(r) > 2 else None
        if r[0] is not None and pub is not None and pub <= cutoff:
            out.append(r[0])
    return out


def _signal_for(signals_json: list, ticker: str) -> Optional[dict]:
    for s in signals_json or []:
        if (s.get("ticker") or "").upper() == ticker:
            return s
    return None


def format_reactions(rows: List[tuple], moves: Dict[int, float], ticker: str,
                     lookback_days: int) -> Dict[str, Any]:
    """Pure: agent_results rows + {news_id: move_1d} → graded-reaction summary.
    A `hit` = predicted direction matches the sign of the realized 1-day move.

    `moves` is expected to be maturity-filtered by the caller (see MOVE_MATURITY_DAYS);
    an event with no entry there is reported with a null move and excluded from the
    hit-rate denominator."""
    events, hits, graded = [], 0, 0
    for news_id, title, published, signals_json in rows:
        sig = _signal_for(signals_json, ticker)
        if not sig:
            continue
        predicted = (sig.get("signal") or "").lower()
        move = moves.get(news_id)
        hit = None
        if move is not None and predicted in ("buy", "sell") and move != 0:
            hit = (move > 0) if predicted == "buy" else (move < 0)
            graded += 1
            hits += bool(hit)
        events.append({"published": str(published), "title": (title or "")[:120],
                       "predicted": predicted, "confidence": sig.get("confidence"),
                       "realized_move_1d_pct": move, "hit": hit})
    if not events:
        return {"available": False,
                "note": f"no intraday calls on this ticker in the last {lookback_days}d as-of"}
    out = {"available": True, "ticker": ticker, "window_days": lookback_days,
           "n_events": len(events), "events": events[:12]}
    if graded:
        out["graded"] = graded
        out["hit_rate"] = round(hits / graded, 2)
    return out


def _realized_moves(neo4j_manager, ticker: str, news_ids: List[int]) -> Dict[int, float]:
    """{news_id: move_1d %} from the graph (event SOURCED_FROM article, AFFECTS company)."""
    if neo4j_manager is None or not news_ids:
        return {}
    driver = getattr(neo4j_manager, "_driver", None)
    if driver is None:
        return {}
    q = """MATCH (a:Article)<-[:SOURCED_FROM]-(:Event)-[r:AFFECTS]->(:Company {ticker: $tk})
           WHERE a.news_id IN $ids AND r.move_1d IS NOT NULL
           RETURN a.news_id AS nid, avg(r.move_1d) AS mv"""
    try:
        with driver.session() as s:
            return {rec["nid"]: round(float(rec["mv"]), 2)
                    for rec in s.run(q, tk=ticker, ids=news_ids)}
    except Exception as e:
        print(f"  ⚠ intraday bridge: graph moves failed ({e})")
        return {}


def read_intraday_reactions(db_manager, neo4j_manager, ticker: str, as_of,
                            lookback_days: int = 60, limit: int = 25) -> Dict[str, Any]:
    """AS-OF window of the intraday agent's calls on `ticker` + realized 1-day moves."""
    tk = ticker.upper()
    start = as_of - timedelta(days=lookback_days)
    conn = psycopg2.connect(**db_manager.db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT news_id, news_title, news_published, signals_json
                FROM agent_results
                WHERE news_published <= %s AND news_published >= %s
                  AND signals_json @> %s
                ORDER BY news_published DESC LIMIT %s
            """, [as_of, start, Json([{"ticker": tk}]), limit])
            rows = cur.fetchall()
    except Exception as e:
        return {"available": False, "note": f"agent_results read failed: {e}"}
    finally:
        conn.close()
    # Only events whose 1-day reaction had fully played out by as_of may contribute a
    # realized move; the rest are never looked up, so they cannot leak one.
    moves = _realized_moves(neo4j_manager, tk, mature_move_ids(rows, as_of))
    return format_reactions(rows, moves, tk, lookback_days)
