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

# --- 3 роли моделей. Меняй через .env, дефолты — fallback. ---
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-3-flash-preview")
GRAPH_LLM_MODEL = os.getenv("GRAPH_LLM_MODEL", "google/gemini-3.1-flash-lite-preview")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "google/gemini-embedding-001")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
