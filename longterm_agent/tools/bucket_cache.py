"""Read/write for bucket_summary_cache (see create_bucket_cache_table.sql).

Caches the per-week map-step output so the replay summarizes each (ticker, week) once
and reuses it across overlapping cohorts. Fresh connections (robust to pool staleness).
"""
import os
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import Json

_ENSURED = False


def _conn(db_manager):
    return psycopg2.connect(**db_manager.db_config)


def ensure_cache_table(db_manager):
    global _ENSURED
    if _ENSURED:
        return
    sql_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "create_bucket_cache_table.sql")
    try:
        with open(sql_path) as f:
            ddl = f.read()
        conn = _conn(db_manager)
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        conn.close()
        _ENSURED = True
    except Exception as e:
        print(f"  ⚠ could not ensure bucket cache table: {e}")


def get_cached(db_manager, ticker, week_start, agent_version) -> Optional[Dict[str, Any]]:
    ensure_cache_table(db_manager)
    try:
        conn = _conn(db_manager)
        with conn.cursor() as cur:
            cur.execute("""SELECT data FROM bucket_summary_cache
                           WHERE ticker=%s AND week_start=%s AND agent_version=%s""",
                        [ticker.upper(), week_start, agent_version])
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def put_cached(db_manager, ticker, week_start, agent_version, data: Dict[str, Any]):
    ensure_cache_table(db_manager)
    try:
        conn = _conn(db_manager)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO bucket_summary_cache (ticker, week_start, agent_version, data)
                           VALUES (%s,%s,%s,%s)
                           ON CONFLICT (ticker, week_start, agent_version) DO UPDATE SET data=EXCLUDED.data""",
                        [ticker.upper(), week_start, agent_version, Json(data)])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠ bucket cache write failed: {e}")
