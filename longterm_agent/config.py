"""Configuration for the long-term agent. Reuses model_config.py (single point for models)."""
import os
import sys
from pathlib import Path

import pytz

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
    for p in (_REPO_ROOT / ".env", Path(__file__).parent / ".env"):
        if p.exists():
            load_dotenv(p)
            break
except ImportError:
    pass

from model_config import (
    LLM_MODEL as _LLM_MODEL,
    GRAPH_LLM_MODEL as _GRAPH_LLM_MODEL,
    EMBEDDING_MODEL as _EMBEDDING_MODEL,
    EMBEDDING_DIMENSIONS as _EMBEDDING_DIMENSIONS,
    OPENROUTER_BASE_URL as _OPENROUTER_BASE_URL,
)

AGENT_VERSION = "0.1.0"


class LongTermAgentConfig:
    """Config for the long-term news-background + fundamentals agent."""

    AGENT_NAME = "longterm_agent"
    AGENT_VERSION = AGENT_VERSION

    # --- Windows (see plan §8: start at 60d, raise to 90d as news history accrues) ---
    LOOKBACK_DAYS_BACKGROUND = int(os.getenv("LT_LOOKBACK_DAYS", "60"))
    SHIFT_WINDOW_DAYS = int(os.getenv("LT_SHIFT_WINDOW_DAYS", "14"))
    BUCKET_DAYS = int(os.getenv("LT_BUCKET_DAYS", "7"))
    MIN_ARTICLES_FOR_BACKGROUND = int(os.getenv("LT_MIN_ARTICLES", "8"))

    # --- Shift / signal thresholds ---
    SENTIMENT_SHIFT_Z_THRESHOLD = float(os.getenv("LT_SHIFT_Z", "1.0"))
    LOOKBACK_DAYS_TECHNICAL_LT = int(os.getenv("LT_TECH_DAYS", "180"))
    EARNINGS_RISK_DAYS = int(os.getenv("LT_EARNINGS_RISK_DAYS", "10"))
    LONGTERM_CONFIDENCE_THRESHOLD = float(os.getenv("LT_CONF_THRESHOLD", "0.55"))
    MAX_REFLECTION_ITERATIONS = int(os.getenv("LT_MAX_REFLECTION", "1"))

    # --- Corpus retrieval ---
    AFFECTS_MIN_CONFIDENCE = float(os.getenv("LT_AFFECTS_MIN_CONF", "0.5"))

    # --- Models (OpenRouter) ---
    LLM_MODEL = _LLM_MODEL              # main reasoning model (reduce, shift, synthesis)
    MAP_MODEL = _GRAPH_LLM_MODEL        # cheap model for per-bucket map summaries
    EMBEDDING_MODEL = _EMBEDDING_MODEL
    EMBEDDING_DIMENSIONS = _EMBEDDING_DIMENSIONS
    OPENROUTER_BASE_URL = _OPENROUTER_BASE_URL
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    LLM_TEMPERATURE = 0
    LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LT_MAX_OUTPUT_TOKENS", "2048"))

    # --- Database ---
    DB_CONFIG = {
        "host": os.getenv("DB_HOST"),
        "database": os.getenv("DB_NAME", "trade_agent"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": os.getenv("DB_PORT", "5432"),
    }

    TIMEZONE = pytz.timezone("America/New_York")

    # --- LangSmith tracing (off unless enabled; sends prompts to LangSmith cloud) ---
    LANGCHAIN_ENDPOINT = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
    LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "longterm-trading-agent")

    # --- Ops / Phase 6 (weekly scheduler, Telegram alerts, stats bot) ---
    # Weekly batch cadence (ET). Run after the weekly fundamentals fetch so data is fresh.
    BATCH_WEEKDAY = os.getenv("LT_BATCH_WEEKDAY", "sunday").lower()
    BATCH_TIME = os.getenv("LT_BATCH_TIME", "06:00")          # ET, off-market
    GRADE_TIME = os.getenv("LT_GRADE_TIME", "07:00")          # daily grading of matured cohorts
    RUN_ON_START = os.getenv("LT_RUN_ON_START", "false").lower() == "true"
    # Telegram alerts (batch job → push) and the stats bot. Falls back to the shared bot token.
    TELEGRAM_BOT_TOKEN = os.getenv("LT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("LT_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
    TELEGRAM_ALLOWED_USER_IDS = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")  # bot auth (comma-sep)
    ALERT_ON_ERROR = os.getenv("LT_ALERT_ON_ERROR", "true").lower() == "true"

    @classmethod
    def get_universe(cls):
        """Default ticker universe for the weekly batch (the curated, news-named set)."""
        env = os.getenv("LT_UNIVERSE", "").strip()
        if env:
            return [t.strip().upper() for t in env.split(",") if t.strip()]
        try:
            from bloomberg_news_fetcher.price_fetcher import TOP_50_TICKERS
            return list(TOP_50_TICKERS)
        except Exception:
            from longterm_agent.company_names import COMPANY_NAMES
            return list(COMPANY_NAMES.keys())

    @classmethod
    def get_db_config(cls):
        return dict(cls.DB_CONFIG)

    @classmethod
    def enable_tracing(cls):
        """Turn on LangSmith tracing for this process. NOTE: exports prompts (news text,
        financials, model reasoning) to the LangSmith cloud. Returns True if enabled."""
        if not cls.LANGCHAIN_API_KEY:
            print("⚠ LANGCHAIN_API_KEY not set — tracing NOT enabled")
            return False
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_ENDPOINT"] = cls.LANGCHAIN_ENDPOINT
        os.environ["LANGCHAIN_API_KEY"] = cls.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = cls.LANGCHAIN_PROJECT
        print(f"✓ LangSmith tracing enabled → project '{cls.LANGCHAIN_PROJECT}'")
        return True

    @classmethod
    def make_llm(cls, model: str = None, max_tokens: int = None):
        """Build a ChatOpenAI client on OpenRouter (same pattern as news_agent).

        max_tokens must leave room for reasoning models (Gemini/o-series burn
        'reasoning_tokens' before any output) — the reduce/synthesis steps need
        several thousand or structured output gets truncated mid-reasoning.
        """
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            api_key=cls.OPENROUTER_API_KEY,
            base_url=cls.OPENROUTER_BASE_URL,
            model=model or cls.LLM_MODEL,
            temperature=cls.LLM_TEMPERATURE,
            max_tokens=min(max_tokens or cls.LLM_MAX_OUTPUT_TOKENS, 8192),
        )
