"""
Embeddings module for generating vector representations of news articles
Uses OpenAI embeddings via Open Router
"""
import os
import sys
import time
from pathlib import Path
from typing import List, Optional
from openai import OpenAI

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import (
    OPENROUTER_BASE_URL,
    EMBEDDING_MODEL as DEFAULT_MODEL,
    EMBEDDING_DIMENSIONS as DEFAULT_DIMENSIONS,
)


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")
    return OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)


class EmbeddingGenerator:
    """Generate embeddings using Open Router"""

    def __init__(self, model_name: str = DEFAULT_MODEL, output_dimensionality: int = DEFAULT_DIMENSIONS):
        """
        Initialize embedding generator

        Args:
            model_name: Open Router embedding model name
            output_dimensionality: Output dimensions (default 1536 for pgvector HNSW compatibility)
        """
        self.model_name = model_name
        self.output_dimensionality = output_dimensionality
        self._client = _get_client()
        print(f"Initialized embedding model: {model_name} ({output_dimensionality} dims)")

    def _embed_with_retry(self, inputs: List[str], max_retries: int = 4):
        """Call the embeddings endpoint, retrying transient failures.

        OpenRouter's embedding endpoint intermittently returns an empty payload
        ("No embedding data received"). Without retries those articles silently
        lose their embedding, which is the main source of NULL-embedding rows.
        """
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = self._client.embeddings.create(
                    input=inputs,
                    model=self.model_name,
                    dimensions=self.output_dimensionality,
                    # Explicit, because the SDK's default is not stable: openai 2.53.0
                    # started sending encoding_format="base64", which Google AI Studio
                    # rejects with a 400. requirements pin `openai>=1.0.0`, so a plain
                    # rebuild changed behaviour with no code change — measured 2026-08-11,
                    # 2.45.0 worked and 2.53.0 did not.
                    encoding_format="float",
                )
                if resp.data:
                    return resp
                last_err = RuntimeError("No embedding data received")
            except Exception as e:
                last_err = e
            if attempt < max_retries:
                time.sleep(min(2 ** (attempt - 1), 4))  # 1s, 2s, 4s, 4s
        print(f"Error generating embedding after {max_retries} attempts: {last_err}")
        raise RuntimeError(str(last_err)) from last_err

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        if not text or text.strip() == '' or text == 'N/A':
            return None
        resp = self._embed_with_retry([text[:20000]])
        return resp.data[0].embedding

    def generate_embeddings_batch(self, texts: List[str], batch_size: int = 5) -> List[Optional[List[float]]]:
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            valid = [(idx, t[:20000]) for idx, t in enumerate(batch) if t and t.strip() and t != 'N/A']
            if not valid:
                results.extend([None] * len(batch))
                continue
            response = self._embed_with_retry([t for _, t in valid])
            emb_map = {idx: response.data[i].embedding for i, (idx, _) in enumerate(valid)}
            results.extend(emb_map.get(j) for j in range(len(batch)))
        return results

    def embed_title_summary(self, title: str, summary: str) -> Optional[List[float]]:
        return self.generate_embedding(f"{title}\n\n{summary}")

    def embed_full_text(self, full_text: str) -> Optional[List[float]]:
        return self.generate_embedding(full_text)


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    import math
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    m1 = math.sqrt(sum(a * a for a in vec1))
    m2 = math.sqrt(sum(b * b for b in vec2))
    if m1 == 0 or m2 == 0:
        return 0.0
    return dot / (m1 * m2)


if __name__ == "__main__":
    print("Testing Open Router Embedding Generator...")
    print("-" * 80)

    generator = EmbeddingGenerator()

    test_text = "Apple announces new iPhone with advanced AI capabilities"
    embedding = generator.generate_embedding(test_text)

    if embedding:
        print("✓ Successfully generated embedding")
        print(f"  Text: {test_text}")
        print(f"  Vector dimensions: {len(embedding)}")
        print(f"  First 5 values: {embedding[:5]}")

        test_text2 = "Apple reveals innovative smartphone with AI features"
        embedding2 = generator.generate_embedding(test_text2)
        if embedding2:
            sim = cosine_similarity(embedding, embedding2)
            print(f"✓ Cosine similarity: {sim:.4f}")
    else:
        print("✗ Failed to generate embedding")
