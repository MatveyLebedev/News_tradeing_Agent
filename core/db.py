"""Postgres access: one connect path with TCP keepalives and retry.

Why keepalives: tool code used to open a FRESH connection per query because pooled
connections idled through multi-minute LLM gaps and were closed server-side (see the
comments this replaces in news_corpus_tool / fundamentals_tool / bucket_cache).
Keepalives make a connection survive those gaps, so either style is safe.

Why retry: the DB is reached over a flaky external link off-server; one transient drop
used to kill a whole multi-ticker run (2026-07-10 backfill incident).
"""
import time
from contextlib import contextmanager

import psycopg2

from core.settings import get_settings

KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def connect(db_config: dict = None, retries: int = 3, delay: float = 2.0, connect_timeout: int = 10):
    """psycopg2 connection with keepalives + backoff retry. Caller closes it."""
    cfg = dict(db_config) if db_config else get_settings().db_config()
    last = None
    for attempt in range(retries):
        try:
            return psycopg2.connect(connect_timeout=connect_timeout, **KEEPALIVE_KWARGS, **cfg)
        except psycopg2.OperationalError as e:
            last = e
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last


@contextmanager
def get_conn(db_config: dict = None, retries: int = 3):
    """`with get_conn() as conn:` — connection is always closed, never leaked."""
    conn = connect(db_config, retries=retries)
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params=None, db_config: dict = None) -> list:
    """One-shot read: list of tuples."""
    with get_conn(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql: str, params=None, db_config: dict = None) -> int:
    """One-shot write inside a committed transaction; returns rowcount."""
    with get_conn(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
        return n
