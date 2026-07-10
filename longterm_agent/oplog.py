"""
Operational logger for the long-term agent (Phase 6).

Writes run-level events and errors to `longterm_agent_log` so the Telegram stats bot
can answer "what ran / what was produced / what failed" from the DB. Best-effort: a
logging failure never crashes a run (falls back to console). Self-contained psycopg2 so
it can be used from any of the ops scripts (batch, scheduler, grading).
"""
import os
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import Json

from longterm_agent.config import LongTermAgentConfig

_RETENTION = int(os.getenv("LT_LOG_RETENTION", "20000"))  # cap rows; trimmed opportunistically
_DDL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "create_oplog_table.sql")
_ensured = False


def _conn():
    return psycopg2.connect(**LongTermAgentConfig.get_db_config())


def ensure_table():
    """Create longterm_agent_log if missing (idempotent, runs once per process)."""
    global _ensured
    if _ensured:
        return
    try:
        with open(_DDL_PATH) as f:
            ddl = f.read()
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        conn.close()
        _ensured = True
    except Exception as e:
        print(f"  ⚠ oplog: could not ensure table: {e}")


def log(level: str, event_type: str, message: str = "",
        ticker: Optional[str] = None, as_of=None, details: Optional[dict] = None) -> bool:
    """Write one log row. Returns True on success; never raises."""
    ensure_table()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO longterm_agent_log (level, event_type, ticker, as_of, message, details)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, [level, event_type, (ticker.upper() if ticker else None),
                  as_of, message[:4000] if message else message,
                  Json(details) if details is not None else None])
            # Opportunistic retention trim (~1% of writes).
            import random
            if random.randint(1, 100) == 1:
                cur.execute("""
                    DELETE FROM longterm_agent_log WHERE id NOT IN (
                        SELECT id FROM longterm_agent_log ORDER BY log_time DESC LIMIT %s
                    )
                """, [_RETENTION])
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  ⚠ oplog write failed ({level} {event_type}): {e} | {message}")
        return False


def info(event_type, message="", **kw):
    return log("INFO", event_type, message, **kw)


def warning(event_type, message="", **kw):
    return log("WARNING", event_type, message, **kw)


def error(event_type, message="", **kw):
    return log("ERROR", event_type, message, **kw)
