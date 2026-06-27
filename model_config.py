"""Единая точка конфигурации LLM-моделей (OpenRouter).

Меняй модели здесь через .env — значения подхватятся во всём проекте.
Три роли:
  - LLM_MODEL        — основная reasoning-модель (анализ новостей, торговые решения)
  - GRAPH_LLM_MODEL  — модель для графовой обработки (извлечение событий, канонизация, merge)
  - EMBEDDING_MODEL  — эмбеддинги для семантического поиска
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

def _clean_env(val: str) -> str:
    """Strip inline '# comments' and surrounding whitespace.

    Docker `--env-file` (and `env_file:` in older compose) keeps everything after
    `=` verbatim, INCLUDING an inline `# comment` — which then gets sent as the
    model name (e.g. 'google/gemini-embedding-001   # ...') and rejected with
    'model does not exist'. Defend against it here so env layout can't break us.
    """
    return val.split("#", 1)[0].strip() if val else val


# --- 3 роли моделей. Меняй через .env, дефолты — fallback. ---
LLM_MODEL = _clean_env(os.getenv("LLM_MODEL", "google/gemini-3-flash-preview"))
GRAPH_LLM_MODEL = _clean_env(os.getenv("GRAPH_LLM_MODEL", "google/gemini-3.1-flash-lite-preview"))
EMBEDDING_MODEL = _clean_env(os.getenv("EMBEDDING_MODEL", "google/gemini-embedding-001"))
EMBEDDING_DIMENSIONS = int(_clean_env(os.getenv("EMBEDDING_DIMENSIONS", "1536")))
