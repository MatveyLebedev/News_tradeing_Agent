"""Single source of configuration: env vars / repo-root .env → one Settings object.

Replaces the seven scattered DB_CONFIG dicts and the root config.py / model_config.py
pair (both are now thin shims over this module). Defaults here are the canonical ones —
notably DB_NAME defaults to `trade_agent` (the live database); the old root config.py
defaulted to `news`, which silently pointed scripts at the wrong database.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Back-compat side effect: much of the codebase still reads os.getenv(...) directly
# (LT_* thresholds, fetcher toggles). pydantic-settings reads .env privately and does
# NOT populate os.environ, so keep the classic load_dotenv until those call sites
# migrate to get_settings(). Removing this breaks every direct os.getenv consumer.
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass


def _strip_inline_comment(value):
    """Strip inline '# comment' from an env value.

    Docker `--env-file` (and `env_file:` in older compose) keeps everything after `=`
    verbatim, INCLUDING an inline `# comment` — which then gets sent as e.g. the model
    name and rejected upstream. Defend here so env layout can't break us.
    """
    if isinstance(value, str):
        return value.split("#", 1)[0].strip()
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Postgres (external ports may differ from defaults; see .env) ---
    db_host: str = "localhost"
    db_name: str = "trade_agent"
    db_user: str = ""
    db_password: str = ""
    db_port: str = "5432"

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # --- LLM roles (OpenRouter). Same three roles as the old model_config.py ---
    # REPRODUCIBILITY: the model id is part of the LLM-cache key and the backtest
    # provenance stamp — changing a model invalidates cache comparability and means two
    # runs are no longer like-for-like. Prefer PINNED, non-`-preview` ids in .env for any
    # run whose results you intend to compare over time (`-preview` endpoints get
    # deprecated under you). These defaults are fallbacks only; live values come from .env.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # main reasoning — no non-preview alias of gemini-3-flash on OpenRouter yet
    # (checked 2026-07-10); a generation jump (e.g. gemini-3.5-flash) would be an
    # eval-gated model change, not a pin — revisit when Google promotes it.
    llm_model: str = "google/gemini-3-flash-preview"
    # graph extraction / map steps — pinned to the stable alias (was -preview).
    # This role feeds the map step whose sentiments feed detection: set it explicitly
    # in the live .env and treat any change as label-affecting (DETECTION_VERSION bump).
    graph_llm_model: str = "google/gemini-3.1-flash-lite"
    embedding_model: str = "google/gemini-embedding-001"
    embedding_dimensions: int = 1536

    # --- Telegram (LT_* variants take precedence for the long-term agent) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    lt_telegram_bot_token: str = ""
    lt_telegram_chat_id: str = ""
    telegram_allowed_user_ids: str = ""

    # --- LangSmith ---
    langchain_api_key: str = ""
    langchain_endpoint: str = "https://api.smith.langchain.com"
    langchain_project: str = "longterm-trading-agent"

    # --- Data-freshness thresholds (core.freshness; cadences in DEPLOY.md §8) ---
    # prices land WEEKLY (Sat 03:00 ET), so by Friday their age is legitimately
    # 5 weekdays — the default must not false-alarm midweek. The Sunday batch
    # preflight overrides this to 2 ("the Saturday fetch must have just happened").
    fresh_news_max_days: int = 2                # parser runs every 5 min
    fresh_prices_max_trading_days: int = 5      # weekly cadence, no midweek false alarms
    fresh_fundamentals_max_days: int = 14       # weekly job + slack for one miss
    fresh_macro_max_days: int = 5               # daily job; advisory-only (never aborts)

    @field_validator(
        "llm_model", "graph_llm_model", "embedding_model", "embedding_dimensions",
        "db_host", "db_name", "db_user", "db_port", mode="before")
    @classmethod
    def _clean(cls, v):
        return _strip_inline_comment(v)

    def db_config(self) -> dict:
        """psycopg2.connect(**kwargs) dict — the shape all existing code expects."""
        return {
            "host": self.db_host,
            "database": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
            "port": self.db_port,
        }

    def telegram_creds(self) -> tuple:
        """(token, chat_id) with LT_* precedence over the shared TELEGRAM_* vars."""
        return (
            self.lt_telegram_bot_token or self.telegram_bot_token,
            self.lt_telegram_chat_id or self.telegram_chat_id,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
