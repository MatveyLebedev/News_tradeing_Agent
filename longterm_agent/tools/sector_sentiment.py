"""Sector daily-sentiment baseline (Phase 13, detection v3).

For a ticker's sector peers, build the MEDIAN across peers of each peer's daily mean
AFFECTS sentiment (graph labels only, ±1/0 — deliberately NOT the hybrid bucket-LLM
fallback: the baseline must be LLM-free so subtracting it never injects map-model
variance into another company's labels).

The v3 break test then runs on the company's EXCESS series (company − baseline):
a sector-wide wave (Broadcom guidance miss dragging every chip name) moves company and
baseline together → excess ≈ 0 → no company shift; an idiosyncratic move stands out.

Process-level cache keyed by (sector, as_of, lookback): within a backfill cohort every
same-sector ticker reuses one computation (peers' series don't depend on which company
is under analysis — the company itself is excluded from ITS baseline at median time by
passing `exclude`).
"""
from datetime import timedelta
from statistics import median
from typing import Any, Dict, Optional

import psycopg2

from ..sector_map import BASELINE_GROUP_OF, BASELINE_GROUPS, peers_for
from .news_corpus_tool import _affects_news_ids, SENTIMENT_NUM

_cache: Dict[tuple, Dict[str, Dict[Any, float]]] = {}


def _peer_daily_means(ticker, as_of, lookback_days, db_manager, neo4j_manager, min_confidence):
    """{date: mean ±1/0 sentiment} for one peer from graph AFFECTS labels, publication-windowed."""
    labels = _affects_news_ids(ticker, neo4j_manager, min_confidence)
    if not labels:
        return {}
    ids = list(labels)
    start = as_of - timedelta(days=lookback_days)
    conn = psycopg2.connect(**db_manager.db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, published FROM news
                           WHERE id = ANY(%s) AND published >= %s AND published <= %s""",
                        [ids, start, as_of])
            rows = cur.fetchall()
    finally:
        conn.close()
    by_day: Dict[Any, list] = {}
    for nid, pub in rows:
        s = SENTIMENT_NUM.get(labels.get(nid))
        if s is None:
            continue
        by_day.setdefault(pub.date(), []).append(s)
    return {d: sum(v) / len(v) for d, v in by_day.items()}


def sector_baseline(ticker: str, as_of, lookback_days: int, db_manager, neo4j_manager,
                    min_confidence: float = 0.5, min_peers: int = 3) -> Optional[Dict[Any, float]]:
    """{date: median-across-peers daily sentiment} for `ticker`'s sector, or None when the
    sector is unmapped / too thin (< min_peers with data) — v3 then degrades to v2 behavior
    for that ticker (excess == company series), which is the honest fallback."""
    tk = ticker.upper()
    sec = BASELINE_GROUP_OF.get(tk)
    if not sec:
        return None
    key = (sec, as_of.date() if hasattr(as_of, "date") else as_of, lookback_days)
    if key not in _cache:
        per_peer = {}
        for p in BASELINE_GROUPS[sec]:
            try:
                series = _peer_daily_means(p, as_of, lookback_days, db_manager,
                                           neo4j_manager, min_confidence)
            except Exception as e:
                print(f"  ⚠ sector baseline: peer {p} failed ({e}); skipped")
                series = {}
            if series:
                per_peer[p] = series
        _cache[key] = per_peer
        if len(_cache) > 64:  # bound memory across long backfills
            _cache.pop(next(iter(_cache)))
    per_peer = {p: s for p, s in _cache[key].items() if p not in ({tk} | _twins(tk))}
    if len(per_peer) < min_peers:
        return None
    days = sorted({d for s in per_peer.values() for d in s})
    return {d: median([s[d] for s in per_peer.values() if d in s]) for d in days}


def _twins(tk):
    from ..sector_map import _TWINS
    return _TWINS.get(tk, set())


def peer_count(ticker: str) -> int:
    return len(peers_for(ticker))
