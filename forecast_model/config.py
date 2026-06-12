import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# forecast использует «лёгкую» модель (flash-lite) — ту же, что графовая обработка (GRAPH_LLM_MODEL)
from model_config import (
    GRAPH_LLM_MODEL as LLM_MODEL,
    EMBEDDING_MODEL,
    EMBEDDING_DIMENSIONS as EMBEDDING_DIMS,
)

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "trade_agent"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432"),
}

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
