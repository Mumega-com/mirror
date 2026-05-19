"""Embedding providers for Mirror.

Set MIRROR_EMBED_PROVIDER=openai, local-onnx, or local. The default is
OpenAI text-embedding-3-small. The local hash provider is deterministic and
keeps tests and offline development working without network credentials.
"""
from __future__ import annotations

import hashlib
import logging
import os

import numpy as np

logger = logging.getLogger("mirror.embeddings")

_DIMS = int(os.getenv("MIRROR_EMBED_DIMS", "1536"))
_local_onnx_model = None


def _embed_openai(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        input=text[:8192],
        dimensions=_DIMS,
    )
    return list(response.data[0].embedding)


def _embed_local_onnx(text: str) -> list[float]:
    global _local_onnx_model
    if _local_onnx_model is None:
        from fastembed import TextEmbedding

        _local_onnx_model = TextEmbedding(os.getenv("MIRROR_LOCAL_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
        logger.info("Local ONNX embedding model loaded")

    embeddings = list(_local_onnx_model.embed([text]))
    emb = [float(x) for x in embeddings[0]]
    if len(emb) < _DIMS:
        emb = emb + [0.0] * (_DIMS - len(emb))
    return emb[:_DIMS]


def _embed_local(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
    rng = np.random.default_rng(seed)
    vec = np.zeros(_DIMS, dtype=np.float32)
    for i in range(max(len(text) - 2, 0)):
        bucket = int(hashlib.md5(text[i:i + 3].encode()).hexdigest(), 16) % _DIMS
        vec[bucket] += 1.0
    vec += rng.standard_normal(_DIMS).astype(np.float32) * 0.01
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec.tolist()


def get_embedding(text: str) -> list[float]:
    provider = os.getenv("MIRROR_EMBED_PROVIDER", "openai").lower()
    providers = {
        "openai": _embed_openai,
        "local-onnx": _embed_local_onnx,
        "local": _embed_local,
    }
    try:
        return providers[provider](text)
    except KeyError:
        raise RuntimeError(f"Unknown MIRROR_EMBED_PROVIDER: {provider}")
    except Exception as exc:
        if provider == "local":
            raise RuntimeError(f"Local embedding provider failed: {exc}") from exc
        logger.warning("Embedding provider %s failed; falling back to local hash: %s", provider, exc)
        return _embed_local(text)
