"""
Configuration for the Trading Agent

All timestamps use US Eastern Time (America/New_York) for consistency with US market hours

Loads secrets from .env file automatically using python-dotenv
"""
import os
from typing import Dict
import pytz
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded environment variables from {env_path}")
    else:
        # Try root directory
        root_env = Path(__file__).parent.parent.parent / '.env'
        if root_env.exists():
            load_dotenv(root_env)
            print(f"✓ Loaded environment variables from {root_env}")
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
except Exception as e:
    print(f"⚠️  Could not load .env file: {e}")

# Единая точка конфигурации моделей — корень репозитория на sys.path
import sys
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import (
    LLM_MODEL as _LLM_MODEL,
    GRAPH_LLM_MODEL as _GRAPH_LLM_MODEL,
    EMBEDDING_MODEL as _EMBEDDING_MODEL,
    EMBEDDING_DIMENSIONS as _EMBEDDING_DIMENSIONS,
    OPENROUTER_BASE_URL as _OPENROUTER_BASE_URL,
)

class AgentConfig:
    """Configuration class for trading agent"""
    
    # Confidence threshold for signals
    CONFIDENCE_THRESHOLD = 0.5
    
    # Lookback periods
    LOOKBACK_DAYS_DEFAULT = int(os.getenv("LOOKBACK_DAYS_DEFAULT", "1"))
    LOOKBACK_DAYS_TECHNICAL = int(os.getenv("LOOKBACK_DAYS_TECHNICAL", "30"))
    LOOKBACK_DAYS_VOLATILITY = int(os.getenv("LOOKBACK_DAYS_VOLATILITY", "30"))
    
    # Risk tolerance parameters
    MAX_STOP_LOSS_PERCENT = float(os.getenv("MAX_STOP_LOSS_PERCENT", "5.0"))
    MIN_TAKE_PROFIT_PERCENT = float(os.getenv("MIN_TAKE_PROFIT_PERCENT", "3.0"))
    RISK_REWARD_RATIO = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
    
    # Stop-loss/take-profit calculation methods
    STOP_LOSS_METHOD = os.getenv("STOP_LOSS_METHOD", "ATR")  # ATR or PERCENTAGE
    ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", "2.0"))
    
    # LLM Configuration (Open Router) — модели берутся из model_config (единая точка / .env)
    LLM_PROJECT = "your-gcp-project-id"
    LLM_MODEL = _LLM_MODEL
    GRAPH_LLM_MODEL = _GRAPH_LLM_MODEL
    LLM_TEMPERATURE = 0
    LLM_MAX_OUTPUT_TOKENS = 1000
    LLM_MAX_TOKENS = 64000
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = _OPENROUTER_BASE_URL

    # LangSmith Configuration
    LANGCHAIN_TRACING_V2 = False
    LANGCHAIN_ENDPOINT = "https://api.smith.langchain.com"
    LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT = "news-trading-agent"
    
    # Database Configuration
    DB_CONFIG: Dict[str, str] = {
        "host": os.getenv("DB_HOST"),
        "database": os.getenv("DB_NAME", "trade_agent"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": os.getenv("DB_PORT", "5432")
    }
    
    # Timezone (US Eastern Time - handles EST/EDT automatically)
    TIMEZONE = pytz.timezone('America/New_York')
    TIMEZONE_NAME = 'America/New_York'
    
    # Market hours (US Eastern Time)
    MARKET_OPEN_HOUR = 9
    MARKET_OPEN_MINUTE = 30
    MARKET_CLOSE_HOUR = 16
    MARKET_CLOSE_MINUTE = 0
    
    # News analysis parameters
    RECENT_NEWS_LOOKBACK_HOURS = int(os.getenv("RECENT_NEWS_LOOKBACK_HOURS", "24"))
    PRICE_MOVEMENT_WINDOW_MINUTES = int(os.getenv("PRICE_MOVEMENT_WINDOW_MINUTES", "40"))
    MIN_TIME_BEFORE_CLOSE_MINUTES = int(os.getenv("MIN_TIME_BEFORE_CLOSE_MINUTES", "60"))
    SIMILAR_NEWS_TOP_K = int(os.getenv("SIMILAR_NEWS_TOP_K", "5"))
    
    # Volatility detection
    VOLUME_SPIKE_MULTIPLIER = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "2.0"))
    HIGH_VOLATILITY_THRESHOLD = float(os.getenv("HIGH_VOLATILITY_THRESHOLD", "1.5"))
    
    # Embedding model (Open Router) — 1536 dims fits pgvector HNSW limit (из model_config)
    EMBEDDING_MODEL = _EMBEDDING_MODEL
    EMBEDDING_DIMENSIONS = _EMBEDDING_DIMENSIONS
    
    # Reflection configuration
    MAX_REFLECTION_ITERATIONS = 1

    # Significance classifier: minimum confidence to treat news as significant.
    # decision.is_significant must be True AND decision.confidence >= this threshold.
    SIGNIFICANCE_CONFIDENCE_THRESHOLD = float(os.getenv("SIGNIFICANCE_CONFIDENCE_THRESHOLD", "0.7"))

    # Minimum novelty (0-1) required to trade. Signals carrying a novelty_score below
    # this are dropped in risk_assessment as "priced-in" (already-known narratives).
    NOVELTY_MIN = float(os.getenv("NOVELTY_MIN", "0.3"))

    # "Already played out" gate: drop a signal if the pre-news price move (last
    # PRE_NEWS_WINDOW_MINUTES) is >= this multiple of the ticker's typical 40-min move
    # AND in the SAME direction as the signal (the catalyst is already in the price).
    PRE_NEWS_PLAYED_OUT_K = float(os.getenv("PRE_NEWS_PLAYED_OUT_K", "1.5"))
    PRE_NEWS_WINDOW_MINUTES = int(os.getenv("PRE_NEWS_WINDOW_MINUTES", "60"))
    # Fail-closed: if there is no pre-news price data for a ticker, drop the signal
    # (no price -> no trade). Set false to fall back to the legacy fail-open behavior.
    PRE_NEWS_REQUIRE_DATA = os.getenv("PRE_NEWS_REQUIRE_DATA", "true").lower() == "true"

    # Tool calling limits — deep reasoning chains need 6-8 iterations
    MAX_TOOL_CALL_ITERATIONS = 7
    
    @classmethod
    def enable_langsmith_tracing(cls):
        """Enable LangSmith tracing by setting environment variables"""
        if cls.LANGCHAIN_TRACING_V2 and cls.LANGCHAIN_API_KEY:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_ENDPOINT"] = cls.LANGCHAIN_ENDPOINT
            os.environ["LANGCHAIN_API_KEY"] = cls.LANGCHAIN_API_KEY
            os.environ["LANGCHAIN_PROJECT"] = cls.LANGCHAIN_PROJECT
            print(f"✓ LangSmith tracing enabled for project: {cls.LANGCHAIN_PROJECT}")
            return True
        return False
    
    @classmethod
    def get_db_config(cls) -> Dict[str, str]:
        """Get database configuration"""
        return cls.DB_CONFIG.copy()



