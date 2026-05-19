"""Shared search helpers for HTTP and MCP callers."""
from __future__ import annotations

from typing import Any, Optional

from kernel.embeddings import get_embedding


def rrf_blend(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion over result dicts keyed by id."""
    scores: dict[str, float] = {}
    for rank, doc in enumerate(vector_results):
        doc_id = str(doc.get("id", ""))
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, doc in enumerate(bm25_results):
        doc_id = str(doc.get("id", ""))
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    all_docs = {str(d.get("id", "")): d for d in vector_results + bm25_results}
    return sorted(all_docs.values(), key=lambda d: scores.get(str(d.get("id", "")), 0.0), reverse=True)


def hybrid_search(
    query: str,
    top_k: int,
    threshold: float,
    workspace_id: Optional[str],
    db: Any,
    **vector_kwargs: Any,
) -> list[dict]:
    """Run vector + BM25 search and blend candidates with RRF."""
    embedding = get_embedding(query)
    internal_limit = top_k * 2
    vector_rows = db.search_engrams(
        embedding=embedding,
        threshold=threshold,
        limit=internal_limit,
        workspace_id=workspace_id,
        **vector_kwargs,
    )
    bm25_rows = (
        db.search_bm25(query=query, limit=internal_limit, workspace_id=workspace_id)
        if hasattr(db, "search_bm25")
        else []
    )
    return rrf_blend(vector_rows, bm25_rows)[:top_k]
