"""DEPRECATED shim — import from core.settings instead.

Единая точка конфигурации LLM-моделей теперь в core/settings.py; этот модуль
сохраняет старые имена (LLM_MODEL и т.д.) для существующих импортов.
"""
from core.settings import get_settings

_s = get_settings()

OPENROUTER_BASE_URL = _s.openrouter_base_url
LLM_MODEL = _s.llm_model
GRAPH_LLM_MODEL = _s.graph_llm_model
STRONG_LLM_MODEL = _s.strong_llm_model
EMBEDDING_MODEL = _s.embedding_model
EMBEDDING_DIMENSIONS = _s.embedding_dimensions
