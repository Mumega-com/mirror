"""Shared embedding utility — imported by mirror_api and task_router to avoid circular imports."""

import logging
import os

logger = logging.getLogger("mirror_api")


def get_embedding(text: str) -> list[float]:
    """Generate embedding using Gemini Embedding API (free tier)."""
    try:
        from google import genai
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text[:8000],
        )
        emb = list(result.embeddings[0].values)
        # Gemini returns 3072 dims; pgvector column is 1536 — first N dims carry most signal
        return emb[:1536]
    except Exception as e:
        logger.error(f"Gemini embedding error: {e}")
        raise RuntimeError(f"Embedding failed: {e}") from e
