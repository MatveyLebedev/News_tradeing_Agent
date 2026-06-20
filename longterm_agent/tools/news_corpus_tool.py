"""
Company news-corpus retrieval (§3.1) for the long-term agent.

Builds the set of articles about ONE company over a period by combining two sources
and deduping by `news.id`:

  1. Neo4j AFFECTS (precision + sentiment) — events that affect the company, restricted
     to COMPANY-domain event types and a confidence floor. This is the precision path:
     the graph's raw AFFECTS is over-broad (macro events fan out to ~32% of the corpus),
     so we drop macro/market event types and low-confidence links. Carries per-article
     sentiment for free.
  2. ILIKE recall — title/summary matches on the company's common name(s) + ticker, to
     catch articles the graph didn't link.

Everything is bounded by [start, end]; `end` = `as_of`, so nothing after the analysis
date leaks in (no lookahead). The weekly bucketing helper turns the corpus into the
per-week structure the background-profiling map-reduce consumes.
"""
import re
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

import psycopg2

from ..company_names import names_for


def _fresh_conn(db_manager):
    # Fresh connection rather than the shared pool: in the replay, many sequential runs
    # leave pooled connections idle across LLM gaps and the server closes them. A short-lived
    # connection per corpus query avoids the stale-connection errors seen between tickers.
    return psycopg2.connect(**db_manager.db_config)

# Company-specific event types only — excludes macro_economic / market_move / commodity_price
# fan-out that pollutes AFFECTS for mega-cap tickers.
COMPANY_EVENT_TYPES = [
    "corporate_announcement", "earnings", "product_launch",
    "merger_acquisition", "leadership_change", "legal", "regulatory",
]

SENTIMENT_NUM = {"positive": 1.0, "bullish": 1.0, "negative": -1.0, "bearish": -1.0, "neutral": 0.0}


def _affects_news_ids(
    ticker: str, neo4j_manager: Any, min_confidence: float
) -> Dict[int, str]:
    """
    Return {news_id: dominant_sentiment} from precision-filtered AFFECTS edges.

    NOTE: we deliberately do NOT filter on e.date here. `e.date` is the event's
    real-world date (an earnings event is dated when earnings happened, a conflict
    when it started — often years before/after the article that covers it). The
    corpus window is a *publication*-date window, applied downstream in Postgres via
    news.published. Filtering on e.date here silently dropped companies whose events
    are dated outside the window (e.g. CAT: 23 in-window articles → 0).
    """
    if neo4j_manager is None:
        return {}
    rows = neo4j_manager.query(
        """
        MATCH (c:Company {ticker:$t})<-[r:AFFECTS]-(e:Event)-[:SOURCED_FROM]->(a:Article)
        WHERE e.type IN $types
          AND coalesce(r.confidence, 0) >= $minconf
        RETURN a.news_id AS news_id, collect(r.sentiment) AS sentiments
        """,
        {"t": ticker.upper(), "types": COMPANY_EVENT_TYPES, "minconf": min_confidence},
    )
    out: Dict[int, str] = {}
    for row in rows:
        nid = row.get("news_id")
        if nid is None:
            continue
        sents = [s for s in (row.get("sentiments") or []) if s]
        if sents:
            # majority sentiment
            out[int(nid)] = max(set(sents), key=sents.count)
        else:
            out[int(nid)] = "neutral"
    return out


def _name_regex(ticker: str) -> Optional[str]:
    """
    Build a Postgres word-boundary regex (`~*`) for the company's names + (safe) ticker.

    Word boundaries (\\y) prevent the substring disaster — bare ILIKE '%CAT%' matched
    "category", "indicate", etc.; `\\yCAT\\y` matches only CAT as a standalone token.
    Bare tickers shorter than 3 chars (T, C, V, KO, GE…) are dropped entirely — even with
    boundaries they collide with stray capital letters ("Plan C", "Series C").
    """
    tokens = list(names_for(ticker))
    if len(ticker) >= 3:
        tokens.append(ticker.upper())
    if not tokens:
        return None
    alternation = "|".join(re.escape(tok) for tok in tokens)
    return rf"\y({alternation})\y"


def _ilike_articles(
    ticker: str, start: datetime, end: datetime, db_manager: Any
) -> List[Dict[str, Any]]:
    """Recall path: articles whose title/summary mention the company name(s) or ticker."""
    rx = _name_regex(ticker)
    if rx is None:
        return []  # no safe name/ticker token → rely on the graph path only
    sql = """
        SELECT id, title, summary, published
        FROM news
        WHERE published >= %s AND published <= %s
          AND (title ~* %s OR summary ~* %s)
        ORDER BY published
    """
    try:
        conn = _fresh_conn(db_manager)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, [start, end, rx, rx])
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "title": r[1], "summary": r[2], "published": r[3]} for r in rows]
    except Exception as e:
        print(f"  ⚠ regex corpus query failed for {ticker}: {e}")
        return []


def _fetch_by_ids(ids: List[int], start: datetime, end: datetime, db_manager: Any) -> List[Dict[str, Any]]:
    if not ids:
        return []
    sql = """
        SELECT id, title, summary, published
        FROM news
        WHERE id = ANY(%s) AND published >= %s AND published <= %s
    """
    try:
        conn = _fresh_conn(db_manager)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, [ids, start, end])
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "title": r[1], "summary": r[2], "published": r[3]} for r in rows]
    except Exception as e:
        print(f"  ⚠ id-fetch corpus query failed: {e}")
        return []


def get_company_news_corpus(
    ticker: str,
    as_of: datetime,
    lookback_days: int,
    db_manager: Any,
    neo4j_manager: Any = None,
    min_confidence: float = 0.5,
) -> Dict[str, Any]:
    """
    Build the company news corpus for [as_of - lookback_days, as_of].

    Returns:
        {
          "ticker": str,
          "articles": [{id, title, summary, published, affects_sentiment|None, sources:[...]}],
          "provenance": {"graph": int, "ilike": int, "both": int, "total": int},
        }
    """
    start = as_of - timedelta(days=lookback_days)

    # graph_map spans all dates; the publication window is applied in Postgres below.
    graph_map = _affects_news_ids(ticker, neo4j_manager, min_confidence)
    ilike = _ilike_articles(ticker, start, as_of, db_manager)
    ilike_ids = {a["id"] for a in ilike}

    # Articles from the graph that ILIKE missed — fetch metadata, windowed by published.
    graph_only_ids = [nid for nid in graph_map if nid not in ilike_ids]
    graph_only = _fetch_by_ids(graph_only_ids, start, as_of, db_manager)

    by_id: Dict[int, Dict[str, Any]] = {}
    for a in ilike:
        a = dict(a)
        a["sources"] = ["ilike"]
        a["affects_sentiment"] = graph_map.get(a["id"])
        if a["id"] in graph_map:
            a["sources"].append("graph")
        by_id[a["id"]] = a
    for a in graph_only:
        a = dict(a)
        a["sources"] = ["graph"]
        a["affects_sentiment"] = graph_map.get(a["id"])
        by_id[a["id"]] = a

    articles = sorted(by_id.values(), key=lambda x: x["published"])
    # Provenance counted over the FINAL (windowed) set, not the all-time graph_map.
    prov = {
        "graph": sum(1 for a in articles if "graph" in a["sources"]),
        "ilike": sum(1 for a in articles if "ilike" in a["sources"]),
        "both": sum(1 for a in articles if len(a["sources"]) > 1),
        "total": len(articles),
    }
    return {"ticker": ticker.upper(), "articles": articles, "provenance": prov}


def bucket_by_week(
    articles: List[Dict[str, Any]], as_of: datetime, lookback_days: int, bucket_days: int = 7
) -> List[Dict[str, Any]]:
    """
    Split the corpus into consecutive `bucket_days` windows ending at `as_of`.

    Returns a list (oldest→newest) of:
        {"index": i, "start": dt, "end": dt, "article_ids": [...],
         "count": n, "affects_sentiments": [...]}
    """
    start = as_of - timedelta(days=lookback_days)
    n_buckets = max(1, (lookback_days + bucket_days - 1) // bucket_days)
    buckets = []
    for i in range(n_buckets):
        b_start = start + timedelta(days=i * bucket_days)
        b_end = min(b_start + timedelta(days=bucket_days), as_of)
        buckets.append({
            "index": i, "start": b_start, "end": b_end,
            "article_ids": [], "count": 0, "affects_sentiments": [],
        })
        if b_end >= as_of:
            break
    for a in articles:
        pub = a["published"]
        offset = (pub - start).days
        bi = min(max(offset // bucket_days, 0), len(buckets) - 1)
        buckets[bi]["article_ids"].append(a["id"])
        buckets[bi]["count"] += 1
        if a.get("affects_sentiment"):
            buckets[bi]["affects_sentiments"].append(a["affects_sentiment"])
    return buckets


if __name__ == "__main__":
    # Coverage measurement (Phase 0 deliverable): per-ticker article counts by source.
    import os
    import sys
    import argparse

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from news_agent.database.db_manager import DBManager
    from graph_db.neo4j_manager import Neo4jManager

    ap = argparse.ArgumentParser(description="Measure news-corpus coverage per ticker")
    ap.add_argument("--tickers", nargs="+", default=["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "SBUX", "CAT", "JPM"])
    ap.add_argument("--as-of", default="2026-06-12")
    ap.add_argument("--lookback", type=int, default=60)
    args = ap.parse_args()

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d")
    db = DBManager({
        "host": os.getenv("DB_HOST"), "database": os.getenv("DB_NAME"), "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"), "port": os.getenv("DB_PORT", "5432"),
    })
    neo = Neo4jManager()
    print(f"\nCorpus coverage  as_of={args.as_of}  lookback={args.lookback}d")
    print(f"{'ticker':8s} {'total':>6s} {'graph':>6s} {'regex':>6s} {'both':>5s}   weekly buckets")
    for tk in args.tickers:
        res = get_company_news_corpus(tk, as_of, args.lookback, db, neo)
        p = res["provenance"]
        b = bucket_by_week(res["articles"], as_of, args.lookback, 7)
        print(f"{tk:8s} {p['total']:6d} {p['graph']:6d} {p['ilike']:6d} {p['both']:5d}   {[x['count'] for x in b]}")
    neo.close()
