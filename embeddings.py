"""
Embedding cascade for Mirror:
  1. gemini-embedding-2-preview  (best — MRL native 1536, multimodal)
  2. gemini-embedding-001         (fallback — proven, free)
  3. local numpy hash             (last resort — deterministic, zero deps, always works)
"""

import hashlib
import logging
import os

import numpy as np

logger = logging.getLogger("mirror_api")

_DIMS = 1536


# ── Tier 1: Gemini Embedding 2 ───────────────────────────────────────────────

def _embed_gemini2(text: str) -> list[float]:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    result = client.models.embed_content(
        model="gemini-embedding-2-preview",
        contents=text[:8192],
        config=types.EmbedContentConfig(output_dimensionality=_DIMS),
    )
    return list(result.embeddings[0].values)


# ── Tier 2: Gemini Embedding 1 ───────────────────────────────────────────────

def _embed_gemini1(text: str) -> list[float]:
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text[:8000],
    )
    emb = list(result.embeddings[0].values)
    return emb[:_DIMS]


# ── Tier 3: Local numpy hash (always works, deterministic) ───────────────────

def _embed_local(text: str) -> list[float]:
    """
    Hash-based deterministic embedding via numpy.
    Not semantic — but stable (same text → same vector) and cosine-compatible.
    Used only when both Gemini tiers are unavailable.
    """
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
    rng = np.random.default_rng(seed)
    # Character-level n-gram hashing into _DIMS buckets for some signal
    vec = np.zeros(_DIMS, dtype=np.float32)
    for i in range(len(text) - 2):
        ngram = text[i:i+3]
        bucket = int(hashlib.md5(ngram.encode()).hexdigest(), 16) % _DIMS
        vec[bucket] += 1.0
    # Blend with seeded noise so unique texts don't collide on same ngrams
    vec += rng.standard_normal(_DIMS).astype(np.float32) * 0.01
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec.tolist()


# ── Public API ────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> list[float]:
    """Cascade: Gemini Embedding 2 → Gemini Embedding 1 → local numpy hash."""
    for tier, fn in [
        ("gemini-embedding-2-preview", _embed_gemini2),
        ("gemini-embedding-001",       _embed_gemini1),
        ("local-numpy-hash",           _embed_local),
    ]:
        try:
            emb = fn(text)
            if tier != "gemini-embedding-2-preview":
                logger.warning(f"Embedding cascade: using {tier}")
            return emb
        except Exception as e:
            logger.error(f"Embedding tier {tier} failed: {e}")

    # _embed_local never raises, but satisfy type checker
    raise RuntimeError("All embedding tiers failed — this should never happen")
