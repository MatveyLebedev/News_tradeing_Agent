"""DEPRECATED shim — import from core.settings instead.

Kept so legacy root scripts (run_agent_with_langsmith.py, simple_agent/…) keep working.
NOTE: DB_NAME now defaults to `trade_agent` (the live DB) via core.settings; the old
default here was `news`, which silently pointed scripts at the wrong database.
"""
from core.settings import get_settings

_s = get_settings()

DB_CONFIG = _s.db_config()

# LangSmith (names preserved verbatim for existing importers)
LANGCHAIN_TRACING_V2 = False
LANGCHAIN_ENDPOINT = _s.langchain_endpoint
LANGCHAIN_API_KEY = _s.langchain_api_key
