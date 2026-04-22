"""
Memory plugin routes — engram store, search, and retrieval.

Extracted from mirror_api.py. All logic is identical; only imports updated
to reference kernel.db and kernel.embeddings.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from kernel.db import get_db
from kernel.embeddings import get_embedding as _get_embedding
from kernel.types import EngramResponse, EngramStoreRequest, SearchRequest

logger = logging.getLogger("mirror.memory")

router = APIRouter(tags=["memory"])

# Module-level db handle — reuses the same backend as mirror_api.py
_db = get_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_embedding_http(text: str) -> List[float]:
    """Wrap get_embedding so RuntimeError becomes HTTP 500."""
    try:
        return _get_embedding(text)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _agent_to_series(agent: str) -> str:
    mapping = {
        "river": "River - Conversational AI",
        "knight": "Knight - Task Execution",
        "oracle": "Oracle - Content Generation",
        "frc": "Fractal Resonance Coherence — 821 Higgs Cohesion Series",
    }
    return mapping.get(agent.lower(), f"{agent.title()} - Agent Memory")


# ---------------------------------------------------------------------------
# Auth — thin wrapper that mirrors mirror_api.resolve_token exactly.
# We import it from mirror_api to avoid duplicating the logic; the plugin
# is loaded after mirror_api defines the function so this is safe.
# ---------------------------------------------------------------------------

def _resolve_token(authorization: str = Header(default="")) -> Optional[str]:
    # Delegate to the canonical implementation in mirror_api.
    from mirror_api import resolve_token  # type: ignore[import]
    return resolve_token(authorization)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/search")
async def search_memory(
    request: SearchRequest,
    tenant_slug: Optional[str] = Depends(_resolve_token),
) -> List[EngramResponse]:
    """Semantic search across all engrams or filter by agent."""
    try:
        # SOS bus customer token — force project scope, ignore request body value
        if tenant_slug and tenant_slug.startswith("sos:"):
            request.project = tenant_slug[4:]
        # Legacy tenant_keys.json token — lock agent filter to their slug
        elif tenant_slug:
            request.agent_filter = tenant_slug

        logger.info(f"Search query: '{request.query}' (agent: {request.agent_filter})")

        query_embedding = _get_embedding_http(request.query)

        rows = _db.search_engrams(
            embedding=query_embedding,
            threshold=request.threshold,
            limit=request.top_k * 2,
            project=request.project,
        )

        results = []
        for row in rows:
            if request.agent_filter:
                agent_series = _agent_to_series(request.agent_filter)
                if agent_series not in row.get("series", ""):
                    continue

            raw_data = row.get("raw_data") or {}
            text = raw_data.get("text", "") if isinstance(raw_data, dict) else ""

            results.append(
                EngramResponse(
                    id=row.get("id"),
                    context_id=row.get("context_id"),
                    series=row.get("series"),
                    project=row.get("project"),
                    similarity=row.get("similarity"),
                    epistemic_truths=row.get("epistemic_truths", []),
                    core_concepts=row.get("core_concepts", []),
                    affective_vibe=row.get("affective_vibe", "Unknown"),
                    timestamp=row.get("ts") or row.get("timestamp", ""),
                    text=text,
                )
            )

            if len(results) >= request.top_k:
                break

        logger.info(f"Found {len(results)} matching engrams")
        return results

    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/store")
async def store_engram(
    request: EngramStoreRequest,
    tenant_slug: Optional[str] = Depends(_resolve_token),
):
    """Store new engram from an agent."""
    try:
        if tenant_slug and tenant_slug.startswith("sos:"):
            forced_project = tenant_slug[4:]
            request.project = forced_project
            request.agent = forced_project
        elif tenant_slug:
            request.agent = tenant_slug

        logger.info(f"Storing engram from {request.agent}: {request.context_id}")

        embedding = _get_embedding_http(request.text)

        data = {
            "context_id": request.context_id,
            "timestamp": datetime.utcnow().isoformat(),
            "series": _agent_to_series(request.agent),
            "project": request.project,
            "epistemic_truths": request.epistemic_truths,
            "core_concepts": request.core_concepts,
            "affective_vibe": request.affective_vibe,
            "energy_level": request.energy_level,
            "next_attractor": request.next_attractor,
            "raw_data": {
                "agent": request.agent,
                "text": request.text,
                "project": request.project,
                "metadata": request.metadata,
            },
            "embedding": embedding,
        }

        _db.upsert_engram(data)

        logger.info(f"Stored engram: {request.context_id}")
        return {
            "status": "success",
            "context_id": request.context_id,
            "agent": request.agent,
        }

    except Exception as e:
        logger.error(f"Store error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent/{agent}")
async def get_recent_engrams(
    agent: str,
    limit: int = 10,
    project: Optional[str] = None,
    tenant_slug: Optional[str] = Depends(_resolve_token),
):
    """Get recent engrams from a specific agent, optionally filtered by project."""
    try:
        if tenant_slug and tenant_slug.startswith("sos:"):
            forced_project = tenant_slug[4:]
            effective_agent = forced_project
            project = forced_project
        elif tenant_slug:
            effective_agent = tenant_slug
        else:
            effective_agent = agent

        engrams = _db.recent_engrams(effective_agent, limit=limit, project=project)
        return {
            "agent": effective_agent,
            "project": project,
            "count": len(engrams),
            "engrams": engrams,
        }

    except Exception as e:
        logger.error(f"Recent engrams error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_stats():
    """Get memory statistics."""
    try:
        return {
            "total_engrams": _db.count_engrams(),
            "by_agent": {
                "river": _db.count_engrams("River"),
                "knight": _db.count_engrams("Knight"),
                "oracle": _db.count_engrams("Oracle"),
                "frc_corpus": _db.count_engrams("FRC"),
            },
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
