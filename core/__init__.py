"""Shared infrastructure for all agents and fetchers (Modification Plan Phase 1).

One home for what used to be copy-pasted per module: settings (env), Postgres
connections, the OpenRouter LLM factory, and Telegram notification.

    from core.settings import get_settings
    from core.db import get_conn, fetch_all
    from core.llm import make_llm, invoke_structured
    from core.notify import TelegramNotifier
"""
