"""
Memory plugin routes — engram store, search, and retrieval.

Extracted from mirror_api.py. Uses kernel.auth.TokenContext for workspace-scoped access.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from kernel.auth import TokenContext, resolve_token_context
from kernel.db import get_db
from kernel.embeddings import get_embedding as _get_embedding
from kernel.types import EngramResponse, EngramStoreRequest, SearchRequest

logger = logging.getLogger("mirror.memory")

router = APIRouter(tags=["memory"])


def _get_db():
    return get_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_embedding_http(text: str) -> List[float]:
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


def _resolve_token(authorization: str = Header(default="")) -> TokenContext:
    return resolve_token_context(authorization)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/search")
async def search_memory(
    request: SearchRequest,
    ctx: TokenContext = Depends(_resolve_token),
) -> List[EngramResponse]:
    """Semantic search across engrams, hard-scoped by workspace_id."""
    try:
        # Non-admin tokens are locked to their workspace
        workspace_id = None if ctx.is_admin else ctx.workspace_id

        logger.info(
            "Search query: '%s' (workspace: %s, admin: %s)",
            request.query, workspace_id, ctx.is_admin,
        )

        query_embedding = _get_embedding_http(request.query)

        rows = _get_db().search_engrams(
            embedding=query_embedding,
            threshold=request.threshold,
            limit=request.top_k * 2,
            project=request.project if ctx.is_admin else None,
            workspace_id=workspace_id,
        )

        results = []
        for row in rows:
            # Agent-slug filter (legacy SOS sos:<project> path, admin-only)
            if ctx.is_admin and request.agent_filter:
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

        logger.info("Found %d matching engrams", len(results))
        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Search error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/store")
async def store_engram(
    request: EngramStoreRequest,
    ctx: TokenContext = Depends(_resolve_token),
):
    """Store new engram, tagged with caller's workspace_id."""
    try:
        # Determine effective workspace and agent from token
        if ctx.is_admin:
            workspace_id = None
            agent = request.agent
            project = request.project
        else:
            workspace_id = ctx.workspace_id
            agent = ctx.owner_id or request.agent
            project = request.project

        logger.info("Storing engram from %s (workspace: %s): %s", agent, workspace_id, request.context_id)

        embedding = _get_embedding_http(request.text)

        data = {
            "context_id": request.context_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "series": _agent_to_series(agent),
            "project": project,
            "workspace_id": workspace_id,
            "owner_type": ctx.owner_type,
            "owner_id": ctx.owner_id,
            "epistemic_truths": request.epistemic_truths,
            "core_concepts": request.core_concepts,
            "affective_vibe": request.affective_vibe,
            "energy_level": request.energy_level,
            "next_attractor": request.next_attractor,
            "raw_data": {
                "agent": agent,
                "text": request.text,
                "project": project,
                "metadata": request.metadata,
            },
            "embedding": embedding,
        }

        _get_db().upsert_engram(data)

        logger.info("Stored engram: %s", request.context_id)
        return {
            "status": "success",
            "context_id": request.context_id,
            "agent": agent,
            "workspace_id": workspace_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Store error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent/{agent}")
async def get_recent_engrams(
    agent: str,
    limit: int = 10,
    project: Optional[str] = None,
    ctx: TokenContext = Depends(_resolve_token),
):
    """Get recent engrams, scoped to caller's workspace."""
    try:
        workspace_id = None if ctx.is_admin else ctx.workspace_id
        effective_agent = agent if ctx.is_admin else (ctx.owner_id or agent)

        engrams = _get_db().recent_engrams(
            effective_agent,
            limit=limit,
            project=project if ctx.is_admin else None,
            workspace_id=workspace_id,
        )
        return {
            "agent": effective_agent,
            "workspace_id": workspace_id,
            "count": len(engrams),
            "engrams": engrams,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Recent engrams error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_stats(ctx: TokenContext = Depends(_resolve_token)):
    """Get memory statistics (admin: global; tenant: workspace-scoped count)."""
    try:
        if ctx.is_admin:
            return {
                "total_engrams": _get_db().count_engrams(),
                "by_agent": {
                    "river": _get_db().count_engrams("River"),
                    "knight": _get_db().count_engrams("Knight"),
                    "oracle": _get_db().count_engrams("Oracle"),
                    "frc_corpus": _get_db().count_engrams("FRC"),
                },
            }
        else:
            return {
                "workspace_id": ctx.workspace_id,
                "total_engrams": _get_db().count_engrams(),
            }
    except Exception as e:
        logger.error("Stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
