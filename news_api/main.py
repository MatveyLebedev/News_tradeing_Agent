"""Read-only HTTP API over the raw `news` table for external consumers.

Built as the Stage-0 input feed for the external news-filtering pipeline
(NEWS_FILTERING_PIPELINE.md): instead of reading a raw-scrape DB directly,
the pipeline pulls increments over HTTP. Field mapping to that document:

    link     -> stable per-article key; the consumer's dedup / idempotency
                key (doc §8 raw_id can be derived from it on their side)
    ts       -> original publication time, ISO8601 UTC (doc §6).
                The DB stores US-Eastern-naive timestamps (see
                bloomberg_news_fetcher/create_table.sql); converted here.
    headline -> news.title
    body     -> best available text: full_text if scraped, else the RSS
                summary; truncated server-side to `body_chars`
    source   -> feed label (bloomberg-technology, cnbc-top,
                marketwatch-top, ...); use it for the section drop (doc §3)
                and source ranking (§5). No opinion feed is fetched.

Auth: X-API-Key header, compared against the NEWS_API_KEY env var.
The service opens the DB with the same DB_* env vars as the fetcher and
issues only parameterized SELECTs.
"""

import hmac
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import FastAPI, Header, HTTPException, Query

# Load .env the same way bloomberg_news_fetcher/config.py does (repo root fallback).
try:
    from dotenv import load_dotenv

    for candidate in (Path(__file__).parent / ".env", Path(__file__).parent.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            break
except ImportError:
    pass  # system env vars only

ET = ZoneInfo("America/New_York")

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "trade_agent"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432"),
}

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    if not os.getenv("NEWS_API_KEY"):
        raise RuntimeError("NEWS_API_KEY is not set — refusing to start an unauthenticated API")
    _pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=4, **DB_CONFIG)
    yield
    _pool.closeall()


app = FastAPI(title="news-api", version="1.0", lifespan=lifespan)


@contextmanager
def db_cursor():
    if _pool is None:
        raise HTTPException(status_code=503, detail="database pool not ready")
    conn = _pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.rollback()  # read-only; drop any implicit transaction
        _pool.putconn(conn)


def require_api_key(x_api_key: Optional[str]) -> None:
    expected = os.getenv("NEWS_API_KEY", "")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def et_naive_to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """DB timestamps are US-Eastern-naive; emit ISO8601 UTC with a Z suffix."""
    if dt is None:
        return None
    return (
        dt.replace(tzinfo=ET)
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def utc_input_to_et_naive(dt: datetime) -> datetime:
    """Query-param timestamps: naive means UTC (the consumer's convention)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).replace(tzinfo=None)


@app.get("/v1/health")
def health():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")


@app.get("/v1/news")
def get_news(
    since_ts: Optional[datetime] = Query(None, description="Window start, inclusive (ISO8601, naive=UTC)"),
    until_ts: Optional[datetime] = Query(None, description="Window end, exclusive (ISO8601, naive=UTC)"),
    ts_field: str = Query(
        "ts",
        pattern="^(ts|fetched)$",
        description="Which time the window filters: 'ts' = publication time, "
        "'fetched' = arrival time (use for incremental polling — late-arriving "
        "articles never escape an arrival-time window)",
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sources: Optional[str] = Query(None, description="Comma-separated source labels to include"),
    body_chars: int = Query(1000, ge=100, le=20000),
    x_api_key: Optional[str] = Header(None),
):
    require_api_key(x_api_key)

    ts_expr = "fetch_time" if ts_field == "fetched" else "COALESCE(published, fetch_time)"
    where = ["TRUE"]
    params: dict = {"limit": limit + 1, "offset": offset, "body_chars": body_chars}
    if sources:
        where.append("source = ANY(%(sources)s)")
        params["sources"] = [s.strip() for s in sources.split(",") if s.strip()]
    if since_ts is not None:
        where.append(f"{ts_expr} >= %(since_et)s")
        params["since_et"] = utc_input_to_et_naive(since_ts)
    if until_ts is not None:
        where.append(f"{ts_expr} < %(until_et)s")
        params["until_et"] = utc_input_to_et_naive(until_ts)

    # full_text can hold a scraper error string ("Error after N attempts: ...",
    # news_fetcher.py) — treat those rows as having no full text.
    # `id` is an internal tiebreaker only (stable offset pagination for equal
    # timestamps); it is not exposed in the response.
    query = f"""
        SELECT link, source, published, fetch_time, title,
               LEFT(
                   CASE WHEN full_text IS NULL OR full_text = ''
                             OR full_text LIKE 'Error after%%'
                        THEN summary ELSE full_text END,
                   %(body_chars)s
               ) AS body
        FROM news
        WHERE {' AND '.join(where)}
        ORDER BY {ts_expr}, id
        LIMIT %(limit)s OFFSET %(offset)s
    """
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            # publication time; falls back to fetch time when the feed gave none
            "ts": et_naive_to_utc_iso(r["published"] or r["fetch_time"]),
            "headline": r["title"],
            "body": r["body"],
            "source": r["source"],
            "link": r["link"],
            "fetched_at": et_naive_to_utc_iso(r["fetch_time"]),
        }
        for r in rows
    ]
    return {
        "count": len(items),
        "has_more": has_more,
        "next_offset": offset + len(items) if has_more else None,
        "items": items,
    }


@app.get("/v1/sources")
def get_sources(x_api_key: Optional[str] = Header(None)):
    """Distinct source labels with volume — feeds the pipeline's ranking config (§5)."""
    require_api_key(x_api_key)
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT source, COUNT(*) AS items, MAX(COALESCE(published, fetch_time)) AS latest
            FROM news GROUP BY source ORDER BY items DESC
            """
        )
        rows = cur.fetchall()
    return {
        "sources": [
            {"source": r["source"], "items": r["items"], "latest_ts": et_naive_to_utc_iso(r["latest"])}
            for r in rows
        ]
    }
