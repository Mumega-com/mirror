"""
Embedding cascade for Mirror:
  1. google:text-embedding-004     (Vertex AI, canonical Mirror model)
  2. SOS kernel EmbeddingAdapter   (vertex → gemini → local, switchable)
  3. gemini-embedding-2-preview    (fallback — MRL native 1536, multimodal)
  4. gemini-embedding-001          (fallback — proven, free)
  5. local ONNX via fastembed      (offline/Pi — semantic, 384 dims, ~90 MB model)
  6. local numpy hash              (last resort — deterministic, zero deps, always works)
"""

import hashlib
import logging
import os
import sys

import numpy as np

logger = logging.getLogger("mirror_api")

# ── SOS kernel adapter (Tier 0) ───────────────────────────────────────────────
# Add SOS to path so Mirror can import without a package install.

_SOS_PATH = os.path.expanduser("~/SOS")
if _SOS_PATH not in sys.path:
    sys.path.insert(0, _SOS_PATH)

try:
    from sos.kernel.embedding_adapter import embed as kernel_embed
    from sos.kernel.embedding_adapter import EmbeddingError as _KernelEmbeddingError
    _KERNEL_AVAILABLE = True
    logger.info("SOS kernel EmbeddingAdapter available — using as Tier 0")
except ImportError as _e:
    _KERNEL_AVAILABLE = False
    kernel_embed = None  # type: ignore[assignment]
    _KernelEmbeddingError = Exception  # type: ignore[assignment,misc]
    logger.warning("SOS kernel EmbeddingAdapter not importable (%s); falling back to Mirror cascade", _e)

_DIMS = 1536
TARGET_EMBEDDING_MODEL = "google:text-embedding-004"
_vertex_model = None


# ── Tier 1: Vertex AI text-embedding-004 ─────────────────────────────────────

def _embed_vertex_text_embedding_004(text: str) -> list[float]:
    """
    Vertex AI text-embedding-004 via Application Default Credentials.
    Google returns 768 dimensions; Mirror zero-pads to 1536 for existing
    pgvector/sqlite-vec index compatibility.
    """
    global _vertex_model
    import vertexai
    from vertexai.language_models import TextEmbeddingModel

    if _vertex_model is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "mumega-com")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        vertexai.init(project=project, location=location)
        _vertex_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        logger.info(
            "Vertex AI text-embedding-004 client initialised (project=%s, location=%s)",
            project,
            location,
        )

    embeddings = _vertex_model.get_embeddings([text[:8192]])
    emb: list[float] = list(embeddings[0].values)
    if len(emb) < _DIMS:
        emb = emb + [0.0] * (_DIMS - len(emb))
    return emb[:_DIMS]


# ── Tier 3: Gemini Embedding 2 ───────────────────────────────────────────────

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


# ── Tier 4: Gemini Embedding 1 ───────────────────────────────────────────────

def _embed_gemini1(text: str) -> list[float]:
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text[:8000],
    )
    emb = list(result.embeddings[0].values)
    return emb[:_DIMS]


# ── Tier 5: Local ONNX via fastembed (offline / Raspberry Pi) ────────────────

_local_onnx_model = None  # lazy-loaded on first use


def _embed_local_onnx(text: str) -> list[float]:
    """
    Local semantic embedding via fastembed + ONNX runtime.
    Model: BAAI/bge-small-en-v1.5 — 384 dims, ~90 MB, CPU-only, Pi-compatible.
    Zero-padded to _DIMS (1536) so vectors stay compatible with existing pgvector
    and sqlite-vec indexes. Cosine similarity is unaffected by trailing zeros.
    """
    global _local_onnx_model
    if _local_onnx_model is None:
        from fastembed import TextEmbedding
        _local_onnx_model = TextEmbedding("BAAI/bge-small-en-v1.5")
        logger.info("Local ONNX model loaded (BAAI/bge-small-en-v1.5, 384 dims)")

    embeddings = list(_local_onnx_model.embed([text]))
    emb: list[float] = [float(x) for x in embeddings[0]]  # 384 Python floats (not numpy.float32)

    # Zero-pad to _DIMS for index compatibility
    if len(emb) < _DIMS:
        emb = emb + [0.0] * (_DIMS - len(emb))

    return emb[:_DIMS]


# ── Tier 6: Local numpy hash (always works, deterministic) ────────────────────

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
    """
    Cascade:
      Tier 1 — google:text-embedding-004 via Vertex AI ADC
      Tier 2 — SOS kernel EmbeddingAdapter (vertex → gemini → local, env-switchable)
      Tier 3 — gemini-embedding-2-preview
      Tier 4 — gemini-embedding-001
      Tier 5 — local ONNX via fastembed (offline / Pi)
      Tier 6 — local numpy hash (deterministic, always works)

    Tier 1 requires Google ADC and network access.
    Tiers 3-4 require GEMINI_API_KEY and network access.
    Tier 5 (fastembed) runs fully offline — ideal for Raspberry Pi.
    Tier 6 is deterministic but not semantic; used only as last resort.
    """
    backend = os.environ.get("MIRROR_EMBEDDING_BACKEND", "auto").lower().strip()

    if backend in {"google", "vertex", "text-embedding-004", TARGET_EMBEDDING_MODEL}:
        return _embed_vertex_text_embedding_004(text)

    if backend == "local":
        return _embed_local_onnx(text)

    if backend == "hash":
        return _embed_local(text)

    if backend == "gemini":
        return _embed_gemini2(text)

    if backend == "gemini1":
        return _embed_gemini1(text)

    if backend == "sos":
        if not _KERNEL_AVAILABLE or kernel_embed is None:
            raise RuntimeError("MIRROR_EMBEDDING_BACKEND=sos but SOS kernel adapter is not available")
        return kernel_embed(text)

    if backend != "auto":
        raise RuntimeError(
            "Unknown MIRROR_EMBEDDING_BACKEND="
            f"{backend!r}; expected auto, google, sos, gemini, gemini1, local, or hash"
        )

    # ── Tier 1: canonical Mirror backend ────────────────────────────────────
    try:
        return _embed_vertex_text_embedding_004(text)
    except Exception as exc:
        logger.warning("%s failed: %s — falling back to Mirror cascade", TARGET_EMBEDDING_MODEL, exc)

    # ── Tier 2: SOS kernel EmbeddingAdapter ─────────────────────────────────
    if _KERNEL_AVAILABLE and kernel_embed is not None:
        try:
            emb = kernel_embed(text)
            return emb
        except Exception as exc:
            logger.warning("Tier 2 (SOS kernel adapter) failed: %s — falling back to Mirror cascade", exc)

    # ── Tiers 3-6: Mirror-local fallback cascade ────────────────────────────
    for tier, fn in [
        ("gemini-embedding-2-preview", _embed_gemini2),
        ("gemini-embedding-001",       _embed_gemini1),
        ("local-onnx-fastembed",       _embed_local_onnx),
        ("local-numpy-hash",           _embed_local),
    ]:
        try:
            emb = fn(text)
            if tier not in ("gemini-embedding-2-preview",):
                logger.warning("Embedding cascade: using %s", tier)
            return emb
        except Exception as e:
            logger.error(f"Embedding tier {tier} failed: {e}")

    # _embed_local never raises, but satisfy type checker
    raise RuntimeError("All embedding tiers failed — this should never happen")
