"""Shared embedding utility — imported by mirror_api and task_router to avoid circular imports."""

import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger("mirror_api")

# Native MRL at 1536 — no truncation, trained dims, matches pgvector column
_MODEL = "gemini-embedding-2-preview"
_DIMS = 1536


def get_embedding(text: str) -> list[float]:
    """Generate embedding using Gemini Embedding 2 (multimodal, MRL, preview)."""
    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        result = client.models.embed_content(
            model=_MODEL,
            contents=text[:8192],
            config=types.EmbedContentConfig(output_dimensionality=_DIMS),
        )
        return list(result.embeddings[0].values)
    except Exception as e:
        logger.error(f"Embedding error ({_MODEL}): {e}")
        raise RuntimeError(f"Embedding failed: {e}") from e
