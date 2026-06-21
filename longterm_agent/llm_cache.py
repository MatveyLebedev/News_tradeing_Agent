"""
Content-addressed LLM response cache (Backtest Plan B0).

Installs a Postgres-backed implementation of LangChain's *global* LLM cache. Because every
node calls the model through `llm.with_structured_output(schema).invoke(prompt)`, and the
global cache intercepts at the chat-model level (below `with_structured_output`), this catches
ALL LLM calls — map, reduce, shift, fundamentals, synthesis, reflection — with **zero node
changes**. The cache key is `hash(model + params + prompt + schema)`; the schema rides in the
serialized call kwargs, so different structured outputs never collide.

Safety: a hit requires a *byte-identical* call (the prompt embeds the news/financial text), so
enabling the cache never returns a stale answer for changed inputs. It is therefore safe to
enable anywhere; it's off by default only to avoid the table on every prod run.

Effect for backtests (the point of B0): the FIRST run of a cohort *records* each unique LLM
call; subsequent runs *replay* from cache → deterministic signals + trade set at ~zero token
cost. That makes a backtest reproducible (you can A/B a config change) and cheap to re-run.

Enable with `LT_LLM_CACHE=true` (entrypoints call `maybe_enable_llm_cache()`), or directly via
`enable_llm_cache()`.
"""
import os
import json
import hashlib
from pathlib import Path
from typing import Any, Optional, Sequence

import psycopg2
from langchain_core.caches import BaseCache
from langchain_core.globals import set_llm_cache, get_llm_cache
from langchain_core.load import dumps, loads

from longterm_agent.config import LongTermAgentConfig

_DDL_PATH = Path(__file__).parent / "create_llm_cache_table.sql"


def _strip_not_implemented(o: Any) -> Any:
    """Replace langchain 'not_implemented' nodes (e.g. with_structured_output's parsed Pydantic
    object stashed in additional_kwargs['parsed']) with None so loads() can revive the payload.
    The raw message content/tool_calls are preserved, so the structured-output parser re-derives
    the object on replay — only the redundant pre-parsed copy is dropped."""
    if isinstance(o, dict):
        if o.get("type") == "not_implemented":
            return None
        return {k: _strip_not_implemented(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_strip_not_implemented(x) for x in o]
    return o


class PostgresLLMCache(BaseCache):
    """LangChain BaseCache backed by Postgres (shared across processes/containers)."""

    def __init__(self):
        self._ensure()

    def _conn(self):
        return psycopg2.connect(**LongTermAgentConfig.get_db_config())

    def _ensure(self):
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(_DDL_PATH.read_text())
        conn.commit()
        conn.close()

    @staticmethod
    def _key(prompt: str, llm_string: str) -> str:
        return hashlib.sha256(f"{llm_string}\x1f{prompt}".encode("utf-8")).hexdigest()

    def lookup(self, prompt: str, llm_string: str) -> Optional[Sequence[Any]]:
        try:
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute("SELECT response FROM longterm_llm_cache WHERE key=%s",
                            [self._key(prompt, llm_string)])
                row = cur.fetchone()
            conn.close()
        except Exception:
            return None  # DB hiccup → miss → recompute (never crash a run)
        if not row:
            return None
        try:
            return loads(row[0])
        except Exception:
            return None  # un-revivable payload → treat as miss

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Any]) -> None:
        try:
            # dumps → strip non-serializable nodes → re-dump, so loads() can revive it later.
            blob = json.dumps(_strip_not_implemented(json.loads(dumps(list(return_val)))))
        except Exception:
            return  # not serializable → skip caching (correctness preserved, just no speedup)
        try:
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO longterm_llm_cache (key, llm, response)
                               VALUES (%s, %s, %s)
                               ON CONFLICT (key) DO UPDATE SET response=EXCLUDED.response""",
                            [self._key(prompt, llm_string), llm_string[:120], blob])
            conn.commit()
            conn.close()
        except Exception:
            pass

    def clear(self, **kwargs: Any) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE longterm_llm_cache")
        conn.commit()
        conn.close()


def enable_llm_cache(force: bool = False) -> bool:
    """Install the Postgres LLM cache as LangChain's global cache. Idempotent."""
    if get_llm_cache() is not None and not force:
        return True
    try:
        set_llm_cache(PostgresLLMCache())
        print("✓ LLM response cache ON (longterm_llm_cache) — record-then-replay; runs reproducible")
        return True
    except Exception as e:
        print(f"⚠ could not enable LLM cache: {e}")
        return False


def maybe_enable_llm_cache() -> bool:
    """Enable iff LT_LLM_CACHE=true. Safe no-op otherwise (call from any entrypoint)."""
    if os.getenv("LT_LLM_CACHE", "false").lower() == "true":
        return enable_llm_cache()
    return False


def cache_stats() -> dict:
    """Row count + age span — for the provenance stamp / debugging."""
    try:
        conn = psycopg2.connect(**LongTermAgentConfig.get_db_config())
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM longterm_llm_cache")
            n, lo, hi = cur.fetchone()
        conn.close()
        return {"rows": n, "from": str(lo), "to": str(hi)}
    except Exception as e:
        return {"error": str(e)}
