"""Configuration for the long-term agent. Reuses model_config.py (single point for models)."""
import os
from pathlib import Path

import pytz

_REPO_ROOT = Path(__file__).resolve().parents[1]  # for provenance() git lookup

# model_config is a shim over core.settings, whose import also load_dotenv()s the
# repo-root .env — so the os.getenv(LT_*) reads below see the file values.
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
    # This agent's edge is detecting background SHIFTS. With no shift the matrix has no cell
    # and synthesis conviction is unstable run-to-run, so a "trade" there is not reproducible.
    # When true, no-shift signals are capped below the trade threshold (deterministic
    # not-traded) — the directional lean + thesis stay for information.
    REQUIRE_SHIFT_TO_TRADE = os.getenv("LT_REQUIRE_SHIFT", "true").lower() == "true"

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

    # --- Deep analysis (Phase 7A: bounded ReAct tool-calling enrichment) ---
    # Adaptive depth: the LLM chooses what to investigate per company via read-only tools.
    # OFF in backtest (labels stay deterministic), ON live (richer theses). It enriches the
    # thesis only — it can never change the deterministic shift label or graded outcome.
    DEEP_ANALYSIS_ENABLED = os.getenv("LT_DEEP_ANALYSIS", "true").lower() == "true"
    MAX_TOOL_CALL_ITERATIONS = int(os.getenv("LT_MAX_TOOL_ITERS", "7"))  # same cap as news_agent
    TOOL_RESULT_MAX_CHARS = int(os.getenv("LT_TOOL_RESULT_MAX_CHARS", "3000"))  # truncate tool blobs
    DEEP_ANALYSIS_MODEL = LLM_MODEL  # reuse the main reasoning model

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

    # --- Backtest reproducibility (Backtest Plan B0) ---
    LLM_CACHE = os.getenv("LT_LLM_CACHE", "false").lower() == "true"

    @classmethod
    def provenance(cls):
        """Run profile stamp for backtests — what produced a result, so runs are comparable."""
        rev = "unknown"
        try:
            import subprocess
            rev = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(_REPO_ROOT), stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            pass
        return {
            "agent_version": cls.AGENT_VERSION,
            "git_revision": rev,
            "model": cls.LLM_MODEL,
            "map_model": cls.MAP_MODEL,
            "temperature": cls.LLM_TEMPERATURE,
            "reflection_iters": cls.MAX_REFLECTION_ITERATIONS,
            "llm_cache": cls.LLM_CACHE,
            "lookback_days": cls.LOOKBACK_DAYS_BACKGROUND,
        }

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
        """Build a ChatOpenAI client on OpenRouter — shared factory in core.llm
        (function_calling-forced structured output; see rationale there)."""
        from core.llm import make_llm
        return make_llm(
            model=model or cls.LLM_MODEL,
            max_tokens=max_tokens or cls.LLM_MAX_OUTPUT_TOKENS,
            temperature=cls.LLM_TEMPERATURE,
        )
