"""Query-text embedding on OpenRouter — the SAME model/dimensions that produced
`news.embedding` (settings.embedding_model), so pgvector cosine search is meaningful.

For short query strings only (corpus retrieval seeds, similarity lookups). Results are
memoized per process: the corpus path embeds ONE fixed string per ticker, so a batch
run pays one API call per ticker regardless of cohort count.
"""
import time
from functools import lru_cache
from typing import Optional

from core.settings import get_settings

_RETRIES = 4
_BACKOFF_S = 2.0


@lru_cache(maxsize=1024)
def embed_text(text: str) -> Optional[tuple]:
    """Embed one string → tuple of floats (hashable → cacheable).

    Returns None only when no API key is configured (feature unavailable). A transient
    API failure RETRIES with backoff and then RAISES — for detection v2 the corpus
    composition is a label input, so a silent empty result would make labels depend on
    API weather. Failures are not cached (lru_cache skips raised calls), successes are.
    """
    s = get_settings()
    if not s.openrouter_api_key:
        return None
    from langchain_openai import OpenAIEmbeddings  # heavy import, keep lazy
    emb = OpenAIEmbeddings(
        api_key=s.openrouter_api_key,
        base_url=s.openrouter_base_url,
        model=s.embedding_model,
        dimensions=s.embedding_dimensions,
        check_embedding_ctx_length=False,
    )
    last = None
    for attempt in range(_RETRIES):
        try:
            return tuple(emb.embed_query(text))
        except Exception as e:  # OpenRouter embeddings endpoint is intermittently flaky
            last = e
            if attempt < _RETRIES - 1:
                time.sleep(_BACKOFF_S * (attempt + 1))
    raise RuntimeError(f"embed_text failed after {_RETRIES} attempts: {last}")
