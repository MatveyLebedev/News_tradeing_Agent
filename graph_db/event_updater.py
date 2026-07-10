"""
Event updater: extract events from a news article and persist them to Neo4j.

Flow per article:
  Stage 0 — canonical_id exact match (no LLM, ~60-70% duplicates)
  Stage 1 — pgvector cosine search (date ±21 days, top-10)
             > 0.88  → auto-merge (no LLM)
             0.65–0.88 → LLM decides merge/create
             < 0.65  → CREATE new event
  Story assignment — pure pgvector ANN search across ALL active story centroids + batch LLM
             >= 0.90  → auto-merge (no LLM)
             0.65–0.90 → batch LLM decides merge/create
             < 0.65   → CREATE new story (LLM generates canonical label)
"""
import os
import re
import sys
import uuid
import logging
import psycopg2
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import GRAPH_LLM_MODEL, EMBEDDING_MODEL

from .event_extractor import extract_events, ExtractedEvent, ExtractionResult
from .neo4j_manager import Neo4jManager

logger = logging.getLogger(__name__)

# historical_data_* tables store tz-naive timestamps in US/Eastern (market hours).
# All price-movement baselines must be converted to the same ET wall-clock.
EASTERN = ZoneInfo("America/New_York")


def _to_et_naive(dt_value) -> datetime | None:
    """Parse a Neo4j datetime string / datetime into a tz-naive ET datetime.

    Aware values (UTC 'Z' or with offset) are converted to US/Eastern wall-clock
    so they line up with the naive ET timestamps in historical_data_* tables.
    """
    if dt_value is None:
        return None
    try:
        if isinstance(dt_value, datetime):
            dt = dt_value
        else:
            dt = datetime.fromisoformat(str(dt_value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(EASTERN).replace(tzinfo=None)
    return dt

try:
    from bloomberg_news_fetcher.logger import DBHandler as _DBHandler
    _db_handler = _DBHandler(event_type="GRAPH_DB")
    _db_handler.setLevel(logging.WARNING)
    logger.addHandler(_db_handler)
except Exception:
    pass

# ── Lazy module-level singletons ────────────────────────────────────────────

_neo4j_manager: Neo4jManager | None = None
_pg_conn = None


def _get_neo4j_manager() -> Neo4jManager:
    global _neo4j_manager
    if _neo4j_manager is None:
        _neo4j_manager = Neo4jManager()
    return _neo4j_manager


def _reset_neo4j_manager():
    global _neo4j_manager
    try:
        if _neo4j_manager is not None:
            _neo4j_manager.close()
    except Exception:
        pass
    _neo4j_manager = None


def _get_pg_conn():
    global _pg_conn
    if _pg_conn is not None and not _pg_conn.closed:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT 1")
            return _pg_conn
        except Exception:
            _pg_conn = None
    _pg_conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=int(os.getenv("DB_PORT", 5432)),
    )
    _ensure_pg_table(_pg_conn)
    return _pg_conn


def _ensure_pg_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS graph_event_embeddings (
                event_id TEXT PRIMARY KEY,
                embedding vector(1536),
                event_date DATE
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS gee_date_idx ON graph_event_embeddings (event_date);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS graph_story_centroids (
                story_key TEXT PRIMARY KEY,
                centroid vector(1536),
                event_count INT DEFAULT 1,
                last_updated TIMESTAMP DEFAULT NOW(),
                start_date TIMESTAMPTZ,
                status TEXT DEFAULT 'active'
            );
        """)
        # Migrate existing tables that may not have the new columns
        cur.execute("""
            ALTER TABLE graph_story_centroids
                ADD COLUMN IF NOT EXISTS start_date TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS gsc_status_idx ON graph_story_centroids (status);")
        cur.execute("CREATE INDEX IF NOT EXISTS gsc_start_date_idx ON graph_story_centroids (start_date);")
    conn.commit()


# ── Embedding helper ─────────────────────────────────────────────────────────

def _embed_events(events: list[ExtractedEvent]) -> list[list[float] | None]:
    """Batch-embed all events: name + description."""
    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )
    texts = [f"{e.name}\n{e.description}" for e in events]
    try:
        resp = client.embeddings.create(
            input=texts,
            model=EMBEDDING_MODEL,
            dimensions=1536,
        )
        return [item.embedding for item in resp.data]
    except Exception as exc:
        logger.warning("Batch embedding failed: %s", exc)
        return [None] * len(events)


# ── Canonical ID ─────────────────────────────────────────────────────────────

def _make_canonical_id(ev: ExtractedEvent, published_at) -> str:
    period = ev.canonical_period or published_at.strftime("%Y-%m")
    return f"{ev.canonical_entity}_{ev.event_type}_{period}"


# ── pgvector candidate search ─────────────────────────────────────────────────

def _pgvector_candidates(
    embedding: list[float],
    published_at,
    pg_conn,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Return [(event_id, cosine_score), ...] sorted by score desc."""
    if embedding is None:
        return []
    vec_str = "[" + ",".join(map(str, embedding)) + "]"
    pub_iso = published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at)
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT event_id, 1 - (embedding <=> %s::vector) AS score
                FROM graph_event_embeddings
                WHERE event_date BETWEEN %s::date - INTERVAL '21 days' AND %s::date
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, pub_iso, pub_iso, vec_str, limit))
            return [(row[0], float(row[1])) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("pgvector search failed: %s", exc)
        return []


def _store_event_embedding(event_id: str, embedding: list[float] | None, published_at, pg_conn):
    if embedding is None:
        return
    vec_str = "[" + ",".join(map(str, embedding)) + "]"
    pub_date = published_at.date() if hasattr(published_at, "date") else published_at
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO graph_event_embeddings (event_id, embedding, event_date)
                VALUES (%s, %s::vector, %s)
                ON CONFLICT (event_id) DO NOTHING
            """, (event_id, vec_str, pub_date))
        pg_conn.commit()
    except Exception as exc:
        logger.warning("Failed to store event embedding: %s", exc)
        pg_conn.rollback()


# ── Neo4j write helpers ───────────────────────────────────────────────────────

def _merge_into_existing(event_id: str, ev: ExtractedEvent, news_id: int, neo4j_mgr: Neo4jManager, published_at=None):
    pub_str = published_at.isoformat() if hasattr(published_at, "isoformat") else None
    neo4j_mgr.query("""
        MATCH (e:Event {id: $id})
        SET e.updated_at = datetime(),
            e.confidence = CASE WHEN $conf > e.confidence THEN $conf ELSE e.confidence END
        WITH e
        MERGE (a:Article {news_id: $nid})
        MERGE (e)-[r:SOURCED_FROM {news_id: $nid}]->(a)
        ON CREATE SET r.published_at = CASE WHEN $pub IS NOT NULL THEN datetime($pub) ELSE null END
    """, {"id": event_id, "conf": ev.confidence, "nid": news_id, "pub": pub_str})


def _create_event(ev: ExtractedEvent, canonical_id: str, news_id: int,
                   neo4j_mgr: Neo4jManager, published_at) -> str:
    event_id = str(uuid.uuid4())
    # Anchor e.date on the article publish time (full intraday timestamp) so price
    # reactions can be measured. The LLM-extracted ev.date is often date-only
    # (→ midnight, no market data) or a referenced historical date, so it is only
    # a fallback when publish time is unavailable.
    if hasattr(published_at, "isoformat"):
        date_str = published_at.isoformat()
    elif ev.date:
        date_str = ev.date
    else:
        date_str = str(published_at)
    neo4j_mgr.query("""
        CREATE (e:Event {
            id: $id,
            canonical_id: $cid,
            name: $name,
            type: $type,
            description: $desc,
            date: datetime($date),
            confidence: $conf,
            keywords: $kw,
            created_at: datetime(),
            updated_at: datetime()
        })
        MERGE (a:Article {news_id: $nid})
        MERGE (e)-[r:SOURCED_FROM {news_id: $nid}]->(a)
        ON CREATE SET r.published_at = CASE WHEN $pub IS NOT NULL THEN datetime($pub) ELSE null END
    """, {
        "id": event_id,
        "cid": canonical_id,
        "name": ev.name,
        "type": ev.event_type,
        "desc": ev.description,
        "date": date_str,
        "conf": ev.confidence,
        "kw": list(_keywords(ev.name + " " + ev.description)),
        "nid": news_id,
        "pub": published_at.isoformat() if hasattr(published_at, "isoformat") else None,
    })
    return event_id


def _link_companies(event_id: str, ev: ExtractedEvent, news_id: int, neo4j_mgr: Neo4jManager):
    for ticker in ev.tickers:
        neo4j_mgr.query("""
            MERGE (c:Company {ticker: $ticker})
            WITH c
            MATCH (e:Event {id: $eid})
            MERGE (e)-[r:AFFECTS]->(c)
            ON CREATE SET r.sentiment = $sentiment,
                          r.confidence = $conf,
                          r.evidence_text = $quote
        """, {
            "ticker": ticker.upper(),
            "eid": event_id,
            "sentiment": ev.sentiment,
            "conf": ev.confidence,
            "quote": ev.source_quote or "",
        })


MOVE_WINDOWS = {"move_15m": 15, "move_40m": 40, "move_4h": 240, "move_1d": 1440}


def _ticker_table(ticker: str) -> str:
    return "historical_data_" + ticker.lower().replace("-", "_").replace(".", "_")


def _query_moves(ticker: str, pub_dt: datetime, pg_conn) -> dict:
    """Return {prop: pct_return} for all windows with available price data.

    Baseline = first bar at/after pub_dt. For each window the end bar is the first
    bar at/after pub_dt+window. If the end bar is the same bar as the baseline
    (e.g. after-hours/weekend news where both resolve to the next session open),
    the window is skipped instead of recording a misleading 0.0% move.
    """
    table = _ticker_table(ticker)
    moves = {}
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                f"SELECT timestamp, close FROM {table} WHERE timestamp >= %s ORDER BY timestamp ASC LIMIT 1",
                (pub_dt,),
            )
            row = cur.fetchone()
            if not row or row[1] is None:
                return moves
            base_ts, baseline = row[0], float(row[1])
            for prop, minutes in MOVE_WINDOWS.items():
                cur.execute(
                    f"SELECT timestamp, close FROM {table} WHERE timestamp >= %s ORDER BY timestamp ASC LIMIT 1",
                    (pub_dt + timedelta(minutes=minutes),),
                )
                end_row = cur.fetchone()
                if end_row and end_row[1] is not None and end_row[0] != base_ts:
                    moves[prop] = round((float(end_row[1]) - baseline) / baseline * 100, 4)
    except Exception as exc:
        logger.debug("Price movement query failed for %s: %s", ticker, exc)
    return moves


def update_price_movements(neo4j_mgr: Neo4jManager | None = None, pg_conn=None):
    """Background process: fill move_* on AFFECTS and SOURCED_FROM edges where still null.

    Should be called after historical_data_{ticker} tables are populated by the price fetcher,
    not during news ingestion (price data may not be available yet at ingestion time).
    """
    if neo4j_mgr is None:
        neo4j_mgr = _get_neo4j_manager()
    if pg_conn is None:
        pg_conn = _get_pg_conn()

    # ── 1. AFFECTS edges with missing moves ──────────────────────────────────
    # Baseline = earliest SOURCED_FROM.published_at (real intraday publish time),
    # falling back to e.date. e.date is frequently date-only (midnight) and has no
    # market data, which previously left every AFFECTS move null.
    affects_rows = neo4j_mgr.query("""
        MATCH (e:Event)-[r:AFFECTS]->(c:Company)
        WHERE r.move_15m IS NULL
        OPTIONAL MATCH (e)-[s:SOURCED_FROM]->(:Article)
        WITH e, r, c, min(s.published_at) AS first_pub
        RETURN e.id AS eid, c.ticker AS ticker,
               toString(coalesce(first_pub, e.date)) AS pub
    """)
    for row in affects_rows:
        pub_dt = _to_et_naive(row["pub"])
        if pub_dt is None:
            continue
        moves = _query_moves(row["ticker"], pub_dt, pg_conn)
        if not moves:
            continue
        set_clause = ", ".join(f"r.{k} = COALESCE(r.{k}, ${k})" for k in moves)
        neo4j_mgr.query(
            f"MATCH (e:Event {{id: $eid}})-[r:AFFECTS]->(c:Company {{ticker: $t}}) SET {set_clause}",
            {"eid": row["eid"], "t": row["ticker"].upper(), **moves},
        )

    # ── 2. SOURCED_FROM edges with missing moves — baseline = r.published_at ─
    sourced_rows = neo4j_mgr.query("""
        MATCH (e:Event)-[r:SOURCED_FROM]->(a:Article)
        WHERE r.move_15m IS NULL AND r.published_at IS NOT NULL
        MATCH (e)-[:AFFECTS]->(c:Company)
        RETURN e.id AS eid, a.news_id AS nid, r.published_at AS pub,
               collect(DISTINCT c.ticker) AS tickers
    """)
    for row in sourced_rows:
        pub_dt = _to_et_naive(row["pub"])
        if pub_dt is None:
            continue
        for ticker in row["tickers"]:
            moves = _query_moves(ticker, pub_dt, pg_conn)
            if not moves:
                continue
            set_clause = ", ".join(f"r.{k} = ${k}_{ticker}" for k in moves)
            params = {"eid": row["eid"], "nid": row["nid"],
                      **{f"{k}_{ticker}": v for k, v in moves.items()}}
            neo4j_mgr.query(
                f"MATCH (e:Event {{id: $eid}})-[r:SOURCED_FROM]->(a:Article {{news_id: $nid}}) SET {set_clause}",
                params,
            )

    logger.info("update_price_movements: processed %d AFFECTS, %d SOURCED_FROM rows",
                len(affects_rows), len(sourced_rows))


def _create_relation(from_id, to_id, rel, neo4j_mgr: Neo4jManager):
    if not from_id or not to_id:
        return
    neo4j_mgr.query(f"""
        MATCH (a:Event {{id: $fid}}), (b:Event {{id: $tid}})
        MERGE (a)-[r:{rel.rel_type}]->(b)
        ON CREATE SET r.confidence = $conf,
                      r.evidence_text = $evidence
    """, {
        "fid": from_id,
        "tid": to_id,
        "conf": rel.confidence,
        "evidence": rel.evidence_text,
    })


# ── LLM linker (stage 1, medium cosine) ──────────────────────────────────────

def _llm_link(ev: ExtractedEvent, candidates: list[tuple[str, float]], article: dict, neo4j_mgr: Neo4jManager) -> str:
    """Ask LLM whether new event merges into an existing one or is new."""
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel

    class LinkDecision(BaseModel):
        merge_into: str | None = None  # event_id to merge into, or None to create

    cand_details = neo4j_mgr.query("""
        MATCH (e:Event) WHERE e.id IN $ids
        RETURN e.id AS id, e.name AS name, e.description AS desc, e.date AS date
    """, {"ids": [eid for eid, _ in candidates]})

    cand_text = "\n".join(
        f"[{c['id']}] {c['name']} — {c['desc']}" for c in cand_details
    )
    prompt = (
        f"New event: {ev.name}\n{ev.description}\n\n"
        f"Existing candidates:\n{cand_text}\n\n"
        "Does the new event describe the SAME real-world fact as one of the candidates? "
        "If yes, return its event_id in merge_into. If it is a distinct event, return null."
    )
    llm = ChatOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model=GRAPH_LLM_MODEL,
        temperature=0,
    ).with_structured_output(LinkDecision)
    try:
        decision: LinkDecision = llm.invoke(prompt)
        return decision.merge_into
    except Exception as exc:
        logger.warning("LLM linker failed: %s", exc)
        return None


# ── Story assignment (pure pgvector + batch LLM) ──────────────────────────────

STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "of", "for", "and", "or",
    "to", "is", "are", "was", "has", "by", "with", "as", "its",
    "from", "that", "this", "it", "be",
}

# Temporal window (days) for how far back to search for an existing story to join.
# Longer windows for narratives that unfold over months.
EVENT_TEMPORAL_WINDOWS = {
    "earnings": 7,
    "dividend": 3,
    "product_launch": 21,
    "merger_acquisition": 90,
    "legal": 180,
    "leadership_change": 14,
    "geopolitical_conflict": 90,
    "trade_policy": 90,
    "macro_economic": 45,
    "commodity_price": 30,
    "market_move": 7,
    "regulatory": 60,
    "corporate_announcement": 14,
    "other": 30,
}

STORY_THRESHOLD_HIGH = float(os.getenv("STORY_THRESHOLD_HIGH", "0.90"))
STORY_THRESHOLD_LOW = float(os.getenv("STORY_THRESHOLD_LOW", "0.65"))
STORY_INACTIVITY_DAYS = int(os.getenv("STORY_INACTIVITY_DAYS", "14"))


def _keywords(text: str) -> set:
    return {w.lower() for w in text.split() if len(w) > 2 and w.lower() not in STOPWORDS}


def _get_domain(event_type: str) -> str:
    mapping = {
        "geopolitical_conflict": "geopolitics",
        "trade_policy": "geopolitics",
        "macro_economic": "macro_markets",
        "commodity_price": "macro_markets",
        "market_move": "macro_markets",
        "regulatory": "regulatory",
        "legal": "regulatory",
    }
    return mapping.get(event_type, "company_events")


def _vec_to_str(vec) -> str:
    return "[" + ",".join(map(str, vec)) + "]"


# ── Story centroid helpers ────────────────────────────────────────────────────

def _init_story_centroid(story_key: str, embedding, pg_conn, start_date=None):
    if embedding is None:
        return
    start_iso = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date) if start_date else None
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO graph_story_centroids (story_key, centroid, event_count, last_updated, start_date, status)
                VALUES (%s, %s::vector, 1, NOW(), %s, 'active')
                ON CONFLICT (story_key) DO NOTHING
            """, (story_key, _vec_to_str(embedding), start_iso))
        pg_conn.commit()
    except Exception as exc:
        logger.warning("Failed to init centroid for %s: %s", story_key, exc)
        pg_conn.rollback()


def _update_story_centroid(story_key: str, embedding, pg_conn):
    if embedding is None:
        return
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT centroid::text, event_count FROM graph_story_centroids WHERE story_key = %s",
                (story_key,),
            )
            row = cur.fetchone()
            if not row:
                _init_story_centroid(story_key, embedding, pg_conn)
                return

            old = np.array([float(x) for x in row[0].strip("[]").split(",")])
            n = row[1]
            new_emb = np.array(embedding, dtype=float)
            updated = (old * n + new_emb) / (n + 1)
            norm = np.linalg.norm(updated)
            if norm > 0:
                updated = updated / norm

            cur.execute("""
                UPDATE graph_story_centroids
                SET centroid = %s::vector, event_count = event_count + 1, last_updated = NOW()
                WHERE story_key = %s
            """, (_vec_to_str(updated), story_key))
        pg_conn.commit()
    except Exception as exc:
        logger.warning("Failed to update centroid for %s: %s", story_key, exc)
        pg_conn.rollback()


# ── Story candidate search (pure pgvector, no domain filter) ─────────────────

def _get_story_candidates(
    ev: ExtractedEvent, published_at, neo4j_mgr: Neo4jManager, pg_conn,
    embedding,
) -> list[dict]:
    """
    Return Story candidates sorted by cosine similarity.
    Uses pgvector ANN search across ALL active story centroids — no domain or
    ticker filter, so cross-domain stories (e.g. Iran war → oil prices) are found.
    """
    if embedding is None:
        return []

    window = EVENT_TEMPORAL_WINDOWS.get(ev.event_type, 30)
    cutoff = (published_at - timedelta(days=window)).isoformat()
    vec_str = _vec_to_str(embedding)

    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT story_key, 1 - (centroid <=> %s::vector) AS score
                FROM graph_story_centroids
                WHERE status = 'active'
                  AND (start_date IS NULL OR start_date >= %s::timestamptz)
                ORDER BY centroid <=> %s::vector
                LIMIT 20
            """, (vec_str, cutoff, vec_str))
            pg_rows = cur.fetchall()
    except Exception as exc:
        logger.warning("Story centroid pgvector search failed: %s", exc)
        return []

    if not pg_rows:
        return []

    keys = [r[0] for r in pg_rows]
    score_map = {r[0]: float(r[1]) for r in pg_rows}

    neo_rows = neo4j_mgr.query("""
        MATCH (s:Story) WHERE s.key IN $keys
        RETURN s.key AS key, s.label AS label, s.article_count AS article_count
    """, {"keys": keys})

    candidates = [
        {
            "key": r["key"],
            "label": r["label"],
            "article_count": r["article_count"],
            "score": score_map.get(r["key"], 0.0),
        }
        for r in neo_rows
    ]
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# ── Story upsert ──────────────────────────────────────────────────────────────

def _upsert_story(event_id, key, label, domain, tickers, published_at, neo4j_mgr, keywords=None):
    neo4j_mgr.query("""
        MERGE (s:Story {key: $key})
        ON CREATE SET s.id = randomUUID(), s.label = $label, s.domain = $domain,
                      s.tickers = $tickers, s.keywords = $keywords,
                      s.start_date = datetime($pub), s.article_count = 1,
                      s.status = 'active', s.updated_at = datetime()
        ON MATCH SET  s.article_count = s.article_count + 1,
                      s.updated_at = datetime()
        WITH s
        MATCH (e:Event {id: $eid})
        MERGE (e)-[:PART_OF]->(s)
    """, {
        "key": key, "label": label, "domain": domain, "tickers": tickers,
        "keywords": keywords or [], "pub": published_at.isoformat(), "eid": event_id,
    })


def _merge_event_into_story(event_id: str, story_key: str, ev: ExtractedEvent, neo4j_mgr: Neo4jManager):
    ev_kw = list(_keywords(ev.name + " " + ev.description))
    neo4j_mgr.query("""
        MATCH (s:Story {key: $key}), (e:Event {id: $eid})
        SET s.article_count = s.article_count + 1,
            s.keywords = s.keywords + $new_kw,
            s.updated_at = datetime()
        MERGE (e)-[:PART_OF]->(s)
    """, {"key": story_key, "eid": event_id, "new_kw": ev_kw})


# ── Canonical label generator ─────────────────────────────────────────────────

def _llm_canonical_label(ev: ExtractedEvent) -> str:
    """Generate a short, broad canonical story label via LLM."""
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel

    class LabelResult(BaseModel):
        label: str

    prompt = (
        "Generate a short (3-6 words) canonical news story label for this event.\n"
        "The label should describe the broad ongoing narrative, not just this specific event.\n"
        "Examples: 'Iran-US Military Tensions', 'Fed Rate Hike Cycle', 'Apple AI Strategy'\n\n"
        f"Event name: {ev.name}\n"
        f"Description: {ev.description}\n"
        "Return only the label, no punctuation at the end."
    )
    llm = ChatOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model=GRAPH_LLM_MODEL,
        temperature=0,
    ).with_structured_output(LabelResult)
    try:
        return llm.invoke(prompt).label
    except Exception as exc:
        logger.warning("LLM canonical label failed: %s", exc)
        return ev.name[:60]


def _create_new_story(event_id, ev, published_at, neo4j_mgr, embedding, pg_conn):
    domain = _get_domain(ev.event_type)
    label = _llm_canonical_label(ev)
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower())[:30].strip("_")
    unique = str(uuid.uuid4())[:8]

    if ev.tickers:
        key = f"{ev.tickers[0]}_{unique}_{slug}"
        tickers = ev.tickers
    else:
        key = f"{domain}_{unique}_{slug}"
        tickers = []

    keywords = list(_keywords(ev.name + " " + ev.description))
    _upsert_story(event_id, key, label, domain, tickers, published_at, neo4j_mgr, keywords=keywords)
    _init_story_centroid(key, embedding, pg_conn, start_date=published_at)
    return key


# ── Batch LLM Story assigner ─────────────────────────────────────────────────

def _llm_assign_stories_batch(
    items: list[tuple[int, ExtractedEvent, list[dict]]],
    article: dict,
    neo4j_mgr: Neo4jManager,
) -> dict[int, dict]:
    """
    One LLM call for all medium-confidence events in an article.
    items: [(idx, ev, story_candidates), ...]
    Returns {idx: {"story_key": str|None, "create_new": bool, "suggested_label": str|None}}
    """
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel
    from typing import List, Optional

    class StoryAssignmentItem(BaseModel):
        event_idx: int
        story_key: Optional[str] = None
        create_new: bool = False
        suggested_label: Optional[str] = None

    class StoryAssignmentBatch(BaseModel):
        assignments: List[StoryAssignmentItem]

    events_text = []
    for idx, ev, candidates in items:
        cands_text = "\n".join(
            f"    [{c['key']}] {c['label']} (articles: {c['article_count']}, score: {c['score']:.2f})"
            for c in candidates[:5]
        )
        events_text.append(
            f"Event [{idx}]: {ev.name}\n"
            f"  Type: {ev.event_type}\n"
            f"  Description: {ev.description}\n"
            f"  Tickers: {ev.tickers or 'none'}\n"
            f"  Candidates:\n{cands_text}"
        )

    prompt = (
        "You are a financial news analyst clustering events into evolving stories.\n\n"
        f"Article: {article.get('title', '')}\n\n"
        + "\n\n".join(events_text)
        + "\n\nFor each event decide: join an existing story (return its key) "
        "or create a new one. Events belong to the same story if they describe "
        "the SAME ongoing narrative. Consider temporal relevance and similarity scores. "
        "Prefer merging into an existing story when score >= 0.70."
    )

    llm = ChatOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model=GRAPH_LLM_MODEL,
        temperature=0,
    ).with_structured_output(StoryAssignmentBatch)

    try:
        result: StoryAssignmentBatch = llm.invoke(prompt)
        return {a.event_idx: a.model_dump() for a in result.assignments}
    except Exception as exc:
        logger.warning("LLM Story batch failed: %s — using score fallback", exc)
        fallback = {}
        for idx, ev, candidates in items:
            best = candidates[0] if candidates else None
            if best and best["score"] >= 0.70:
                fallback[idx] = {"story_key": best["key"], "create_new": False, "suggested_label": None}
            else:
                fallback[idx] = {"story_key": None, "create_new": True, "suggested_label": None}
        return fallback


# ── Story assignment orchestrator ─────────────────────────────────────────────

def _assign_stories_for_article(
    resolved_events: list[tuple[str, ExtractedEvent, list[float] | None]],
    published_at,
    article: dict,
    neo4j_mgr: Neo4jManager,
    pg_conn,
):
    """
    Two-pass Story assignment for all events of one article.
    resolved_events: [(event_id, ev, embedding), ...]
    """
    auto_merge: list[tuple[int, str, str, list[float] | None]] = []
    needs_llm: list[tuple[int, ExtractedEvent, str, list[dict], list[float] | None]] = []
    auto_create: list[tuple[int, str, ExtractedEvent, list[float] | None]] = []

    for idx, (event_id, ev, embedding) in enumerate(resolved_events):
        candidates = _get_story_candidates(ev, published_at, neo4j_mgr, pg_conn, embedding)

        if candidates and candidates[0]["score"] >= STORY_THRESHOLD_HIGH:
            auto_merge.append((idx, event_id, candidates[0]["key"], embedding))
        elif candidates and candidates[0]["score"] >= STORY_THRESHOLD_LOW:
            needs_llm.append((idx, ev, event_id, candidates, embedding))
        else:
            auto_create.append((idx, event_id, ev, embedding))

    # Auto-merge (high confidence)
    for idx, event_id, story_key, embedding in auto_merge:
        ev = resolved_events[idx][1]
        _merge_event_into_story(event_id, story_key, ev, neo4j_mgr)
        _update_story_centroid(story_key, embedding, pg_conn)

    # Batch LLM (medium confidence)
    if needs_llm:
        llm_items = [(idx, ev, cands) for idx, ev, _, cands, _ in needs_llm]
        decisions = _llm_assign_stories_batch(llm_items, article, neo4j_mgr)

        for idx, ev, event_id, candidates, embedding in needs_llm:
            decision = decisions.get(idx, {})
            if decision.get("create_new") or not decision.get("story_key"):
                _create_new_story(event_id, ev, published_at, neo4j_mgr, embedding, pg_conn)
            else:
                _merge_event_into_story(event_id, decision["story_key"], ev, neo4j_mgr)
                _update_story_centroid(decision["story_key"], embedding, pg_conn)

    # Auto-create (low confidence / no candidates)
    for idx, event_id, ev, embedding in auto_create:
        _create_new_story(event_id, ev, published_at, neo4j_mgr, embedding, pg_conn)


# ── Story auto-close ─────────────────────────────────────────────────────────

def close_stale_stories(neo4j_mgr: Neo4jManager | None = None, pg_conn=None):
    """
    Mark Stories as closed if no events were added for STORY_INACTIVITY_DAYS.
    Intended to be called via cron (e.g. daily).
    """
    if neo4j_mgr is None:
        neo4j_mgr = _get_neo4j_manager()
    if pg_conn is None:
        pg_conn = _get_pg_conn()

    closed = neo4j_mgr.query("""
        MATCH (s:Story)
        WHERE coalesce(s.status, 'active') = 'active'
          AND s.updated_at < datetime() - duration({days: $days})
        SET s.status = 'closed'
        RETURN s.key AS key
    """, {"days": STORY_INACTIVITY_DAYS})

    if closed:
        keys = [r["key"] for r in closed]
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE graph_story_centroids SET status = 'closed' WHERE story_key = ANY(%s)",
                    (keys,),
                )
            pg_conn.commit()
        except Exception as exc:
            logger.warning("Failed to update status for closed stories: %s", exc)
            pg_conn.rollback()
        logger.info("Closed %d stale stories", len(keys))


def purge_old_event_embeddings(retention_days: int = 365, pg_conn=None) -> int:
    """
    Delete event embeddings older than `retention_days` to bound graph_event_embeddings
    growth (≈6 KB/row, no TTL otherwise — the dominant ongoing-disk line item).
    retention_days <= 0 disables the purge. Intended to run daily alongside
    close_stale_stories(). Returns the number of rows deleted.
    """
    if retention_days <= 0:
        return 0
    if pg_conn is None:
        pg_conn = _get_pg_conn()
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM graph_event_embeddings "
                "WHERE event_date < (CURRENT_DATE - %s::int)",
                (retention_days,),
            )
            deleted = cur.rowcount
        pg_conn.commit()
        if deleted:
            logger.info(
                "purge_old_event_embeddings: deleted %d rows older than %d days",
                deleted, retention_days,
            )
        return deleted
    except Exception as exc:
        logger.warning("purge_old_event_embeddings failed: %s", exc)
        pg_conn.rollback()
        return 0


# ── Story consolidation (merge duplicates) ────────────────────────────────────

def _llm_confirm_story_merges(
    pairs: list[tuple[str, str, float]],
    label_map: dict[str, str],
) -> list[tuple[str, str, float]]:
    """Ask LLM to confirm which high-similarity story pairs are the same narrative."""
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel
    from typing import List

    class PairDecision(BaseModel):
        idx: int
        same_story: bool

    class MergeConfirmation(BaseModel):
        decisions: List[PairDecision]

    capped = pairs[:20]
    pairs_text = "\n".join(
        f"[{i}] '{label_map.get(a, a)}' vs '{label_map.get(b, b)}' (similarity: {s:.2f})"
        for i, (a, b, s) in enumerate(capped)
    )
    prompt = (
        "You are a financial news analyst. For each numbered pair of story titles, "
        "decide if they describe the SAME ongoing real-world narrative (same_story=true) "
        "or clearly different stories (same_story=false).\n\n"
        + pairs_text
    )
    llm = ChatOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model=GRAPH_LLM_MODEL,
        temperature=0,
    ).with_structured_output(MergeConfirmation)
    try:
        result: MergeConfirmation = llm.invoke(prompt)
        confirmed_idxs = {d.idx for d in result.decisions if d.same_story}
        return [p for i, p in enumerate(capped) if i in confirmed_idxs]
    except Exception as exc:
        logger.warning("LLM merge confirmation failed: %s — auto-confirming by score", exc)
        return capped


def merge_duplicate_stories(
    neo4j_mgr: Neo4jManager | None = None,
    pg_conn=None,
    similarity_threshold: float = 0.88,
):
    """
    Find near-duplicate active stories via pairwise cosine similarity on centroids
    and merge them. Surviving story = the one with higher article_count.
    Intended to run weekly alongside close_stale_stories.
    """
    if neo4j_mgr is None:
        neo4j_mgr = _get_neo4j_manager()
    if pg_conn is None:
        pg_conn = _get_pg_conn()

    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT story_key, centroid::text FROM graph_story_centroids WHERE status = 'active'"
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning("merge_duplicate_stories: centroid load failed: %s", exc)
        return

    if len(rows) < 2:
        logger.info("merge_duplicate_stories: fewer than 2 active stories, nothing to do")
        return

    keys = [r[0] for r in rows]
    vecs = np.array(
        [[float(x) for x in r[1].strip("[]").split(",")] for r in rows],
        dtype=float,
    )

    # L2-normalize
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    vecs = vecs / norms

    # Pairwise cosine similarity (upper triangle only)
    sim_matrix = vecs @ vecs.T
    pairs = [
        (keys[i], keys[j], float(sim_matrix[i, j]))
        for i in range(len(keys))
        for j in range(i + 1, len(keys))
        if sim_matrix[i, j] >= similarity_threshold
    ]

    if not pairs:
        logger.info("merge_duplicate_stories: no duplicate pairs found")
        return

    logger.info("merge_duplicate_stories: %d candidate pairs above %.2f", len(pairs), similarity_threshold)

    all_keys = list({k for p in pairs for k in (p[0], p[1])})
    neo_rows = neo4j_mgr.query(
        "MATCH (s:Story) WHERE s.key IN $keys RETURN s.key AS key, s.label AS label, s.article_count AS cnt",
        {"keys": all_keys},
    )
    label_map = {r["key"]: r["label"] for r in neo_rows}
    count_map = {r["key"]: r["cnt"] for r in neo_rows}

    confirmed = _llm_confirm_story_merges(pairs, label_map)
    merged_count = 0

    for key_a, key_b, score in confirmed:
        cnt_a = count_map.get(key_a, 0)
        cnt_b = count_map.get(key_b, 0)
        survivor, duplicate = (key_a, key_b) if cnt_a >= cnt_b else (key_b, key_a)
        try:
            # Re-point all PART_OF edges from duplicate to survivor
            neo4j_mgr.query("""
                MATCH (e:Event)-[r:PART_OF]->(dup:Story {key: $dup})
                MATCH (surv:Story {key: $surv})
                MERGE (e)-[:PART_OF]->(surv)
                SET surv.article_count = surv.article_count + 1,
                    surv.updated_at = datetime()
                DELETE r
            """, {"dup": duplicate, "surv": survivor})
            neo4j_mgr.query(
                "MATCH (s:Story {key: $key}) SET s.status = 'merged'",
                {"key": duplicate},
            )
            with pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE graph_story_centroids SET status = 'merged' WHERE story_key = %s",
                    (duplicate,),
                )
            pg_conn.commit()

            # Fold the duplicate's centroid into the survivor
            dup_idx = keys.index(duplicate)
            _update_story_centroid(survivor, vecs[dup_idx].tolist(), pg_conn)

            merged_count += 1
            logger.info(
                "Merged story '%s' into '%s' (score=%.3f)",
                label_map.get(duplicate, duplicate),
                label_map.get(survivor, survivor),
                score,
            )
        except Exception as exc:
            logger.warning("Failed to merge %s into %s: %s", duplicate, survivor, exc)
            try:
                pg_conn.rollback()
            except Exception:
                pass

    logger.info("merge_duplicate_stories: merged %d duplicate story pairs", merged_count)


# ── Public entry point ────────────────────────────────────────────────────────

def process_article_events(article: dict, news_id: int,
                            neo4j_mgr: Neo4jManager | None = None,
                            pg_conn=None):
    """
    Extract events from article and persist to Neo4j.
    Called with (article, news_id) from news_fetcher.py — uses lazy singletons.

    Two-pass approach:
      Pass 1 — dedup + create/merge Events
      Pass 2 — batch Story assignment (pure pgvector + LLM)
    """
    try:
        if neo4j_mgr is None:
            neo4j_mgr = _get_neo4j_manager()
        if pg_conn is None:
            pg_conn = _get_pg_conn()
    except Exception as conn_exc:
        _reset_neo4j_manager()
        global _pg_conn
        _pg_conn = None
        logger.warning("Connection setup failed for news_id=%s: %s", news_id, conn_exc)
        return

    try:
        result: ExtractionResult = extract_events(
            article.get("title", ""), article.get("summary", "")
        )
    except Exception as exc:
        logger.warning("Event extraction failed for news_id=%s: %s", news_id, exc)
        return

    if not result.events:
        return

    ev_embeddings = _embed_events(result.events)
    published_at = article.get("published_dt")

    event_id_map: dict[int, str] = {}
    resolved_events: list[tuple[str, ExtractedEvent, list[float] | None]] = []

    # Pass 1: dedup + Event creation
    for idx, ev in enumerate(result.events):
        canonical_id = _make_canonical_id(ev, published_at)
        embedding = ev_embeddings[idx]

        existing = neo4j_mgr.query(
            "MATCH (e:Event {canonical_id: $cid}) RETURN e.id AS id", {"cid": canonical_id}
        )
        if existing:
            event_id = existing[0]["id"]
            _merge_into_existing(event_id, ev, news_id, neo4j_mgr, published_at)
        else:
            candidates = _pgvector_candidates(embedding, published_at, pg_conn)
            high = [(eid, s) for eid, s in candidates if s > 0.88]
            medium = [(eid, s) for eid, s in candidates if 0.65 < s <= 0.88]

            if high:
                event_id = high[0][0]
                _merge_into_existing(event_id, ev, news_id, neo4j_mgr, published_at)
            elif medium:
                merged_id = _llm_link(ev, medium[:5], article, neo4j_mgr)
                if merged_id:
                    event_id = merged_id
                    _merge_into_existing(event_id, ev, news_id, neo4j_mgr, published_at)
                else:
                    event_id = _create_event(ev, canonical_id, news_id, neo4j_mgr, published_at)
            else:
                event_id = _create_event(ev, canonical_id, news_id, neo4j_mgr, published_at)

        _store_event_embedding(event_id, embedding, published_at, pg_conn)
        event_id_map[idx] = event_id
        _link_companies(event_id, ev, news_id, neo4j_mgr)
        resolved_events.append((event_id, ev, embedding))

    # Pass 2: batch Story assignment
    _assign_stories_for_article(resolved_events, published_at, article, neo4j_mgr, pg_conn)

    if article.get("title"):
        neo4j_mgr.query(
            "MERGE (a:Article {news_id: $nid}) SET a.name = $title",
            {"nid": news_id, "title": article["title"]},
        )

    for rel in result.intra_relations:
        _create_relation(
            event_id_map.get(rel.from_idx),
            event_id_map.get(rel.to_idx),
            rel,
            neo4j_mgr,
        )
