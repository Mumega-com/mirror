"""
Memory plugin routes — engram store, search, and retrieval.

Extracted from mirror_api.py. Uses kernel.auth.TokenContext for workspace-scoped access.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from kernel.auth import TokenContext, VALID_TIERS, resolve_token_context
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


def _rrf_blend(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion — merges two ranked lists by doc id."""
    scores: dict[str, float] = {}
    for rank, doc in enumerate(vector_results):
        doc_id = str(doc.get("id", ""))
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, doc in enumerate(bm25_results):
        doc_id = str(doc.get("id", ""))
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    all_docs = {str(d.get("id", "")): d for d in vector_results + bm25_results}
    return sorted(all_docs.values(), key=lambda d: scores.get(str(d.get("id", "")), 0.0), reverse=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/search")
async def search_memory(
    request: SearchRequest,
    ctx: TokenContext = Depends(_resolve_token),
    x_project_context: Optional[str] = Header(default=None),
) -> List[EngramResponse]:
    """Semantic search across engrams, hard-scoped by workspace_id and tier RBAC."""
    try:
        # Non-admin tokens are locked to their workspace
        workspace_id = None if ctx.is_admin else ctx.workspace_id

        # Tier access: admin sees all; otherwise use token-resolved tier_access.
        # Backward-compat: tokens without tier_access field default to ['public', 'project'].
        tier_access = None if ctx.is_admin else (ctx.tier_access or ["public", "project"])
        caller_entity_id = None if ctx.is_admin else ctx.entity_id

        logger.info(
            "Search query: '%s' (workspace: %s, admin: %s, x_project_context: %s, tiers: %s)",
            request.query, workspace_id, ctx.is_admin, x_project_context, tier_access,
        )

        query_embedding = _get_embedding_http(request.query)
        internal_limit = request.top_k * 2
        db = _get_db()

        if x_project_context and not ctx.is_admin:
            # Blend: agent-owned engrams (scoped to this caller) + project-scoped engrams.
            # Admin callers are excluded from this path because ctx.owner_id is None for
            # admin tokens — passing owner_id=None would skip the owner filter and leak
            # agent engrams across all workspaces.
            agent_rows = db.search_engrams(
                embedding=query_embedding,
                threshold=request.threshold,
                limit=internal_limit,
                workspace_id=workspace_id,
                owner_type="agent",
                owner_id=ctx.owner_id,
                tier_access=tier_access,
                caller_entity_id=caller_entity_id,
            )
            project_rows = db.search_engrams(
                embedding=query_embedding,
                threshold=request.threshold,
                limit=internal_limit,
                owner_type="project",
                owner_id=x_project_context,
                tier_access=tier_access,
                caller_entity_id=caller_entity_id,
            )
            # Deduplicate by id then blend via RRF
            seen: set[str] = set()
            merged_rows: list[dict] = []
            for row in agent_rows + project_rows:
                row_id = str(row.get("id", ""))
                if row_id not in seen:
                    seen.add(row_id)
                    merged_rows.append(row)
            vector_rows = merged_rows
        elif x_project_context:
            # Admin caller with X-Project-Context: filter by project only, no blend.
            # We do NOT use owner_id here to avoid cross-tenant leaks.
            vector_rows = db.search_engrams(
                embedding=query_embedding,
                threshold=request.threshold,
                limit=internal_limit,
                workspace_id=workspace_id,
                owner_type="project",
                owner_id=x_project_context,
            )
        else:
            # Hybrid retrieval — vector + BM25, blended with RRF
            vector_rows = db.search_engrams(
                embedding=query_embedding,
                threshold=request.threshold,
                limit=internal_limit,
                project=request.project if ctx.is_admin else None,
                workspace_id=workspace_id,
                tier_access=tier_access,
                caller_entity_id=caller_entity_id,
            )

        bm25_rows = db.search_bm25(
            query=request.query,
            limit=internal_limit,
            workspace_id=workspace_id,
        ) if hasattr(db, "search_bm25") else []

        blended = _rrf_blend(vector_rows, bm25_rows)

        results = []
        for row in blended:
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
                    tier=row.get("tier", "project"),
                    entity_id=row.get("entity_id"),
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
    x_session_id: Optional[str] = Header(default=None),
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

        # Determine owner identity and session classification
        is_session = ctx.owner_type == "session" or x_session_id is not None
        effective_owner_type = "session" if is_session else ctx.owner_type
        effective_owner_id = (x_session_id or ctx.owner_id) if is_session else ctx.owner_id

        logger.info("Storing engram from %s (workspace: %s): %s", agent, workspace_id, request.context_id)

        embedding = _get_embedding_http(request.text)

        # Validate tier if provided
        tier = request.tier or "project"
        if tier not in VALID_TIERS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid tier {tier!r}. Must be one of: {sorted(VALID_TIERS)}",
            )
        # entity_id: use request value, fallback to workspace_id
        entity_id = request.entity_id or workspace_id

        data = {
            "context_id": request.context_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "series": _agent_to_series(agent),
            "project": project,
            "workspace_id": workspace_id,
            "owner_type": effective_owner_type,
            "owner_id": effective_owner_id,
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
            "tier": tier,
            "entity_id": entity_id,
            "permitted_roles": request.permitted_roles or [],
        }

        # Session engrams get low importance so they don't pollute standard recall
        if is_session:
            data["importance_score"] = 0.05
            data["memory_tier"] = "working"

        # Online dedup: if a near-identical engram already exists (cosine > 0.92)
        # merge into it instead of creating a duplicate row.
        db = _get_db()
        merged = False
        if hasattr(db, "search_engrams") and hasattr(db, "merge_engram"):
            near = db.search_engrams(
                embedding=embedding,
                threshold=0.92,
                limit=1,
                workspace_id=workspace_id,
            )
            if near and near[0].get("similarity", 0) >= 0.92:
                existing_id = near[0]["id"]
                db.merge_engram(existing_id, request.text, request.metadata or {})
                logger.info(
                    "Merged duplicate into engram %s (similarity=%.3f)",
                    existing_id, near[0]["similarity"],
                )
                merged = True

        if not merged:
            db.upsert_engram(data)

        logger.info("Stored engram: %s (merged=%s)", request.context_id, merged)
        return {
            "status": "success",
            "context_id": request.context_id,
            "agent": agent,
            "workspace_id": workspace_id,
            "merged": merged,
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
        db = _get_db()
        if ctx.is_admin:
            return {
                "total_engrams": db.count_engrams(),
                "by_agent": {
                    "river": db.count_engrams("River"),
                    "knight": db.count_engrams("Knight"),
                    "oracle": db.count_engrams("Oracle"),
                    "frc_corpus": db.count_engrams("FRC"),
                },
            }
        else:
            # Non-admin callers get a count scoped to their own workspace only.
            # count_engrams_in_workspace ensures mumega-internal is never counted
            # for customer tokens (and customers never count each other's engrams).
            count = (
                db.count_engrams_in_workspace(ctx.workspace_id)
                if hasattr(db, "count_engrams_in_workspace")
                else db.count_engrams()
            )
            return {
                "workspace_id": ctx.workspace_id,
                "total_engrams": count,
            }
    except Exception as e:
        logger.error("Stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Tier promotion endpoint — coordinator role required
# ---------------------------------------------------------------------------

class TierUpdateRequest(BaseModel):
    tier: str


@router.patch("/engrams/{engram_id}/tier")
async def update_engram_tier(
    engram_id: str,
    request: TierUpdateRequest,
    ctx: TokenContext = Depends(_resolve_token),
):
    """Promote or demote an engram's tier. Requires coordinator role.

    Only callers with role='coordinator' (or is_admin=True) may change tiers.
    The new tier must be one of: public, squad, project, entity, private.
    """
    # Authorization: coordinator role or admin required
    if not ctx.is_admin and ctx.role != "coordinator":
        raise HTTPException(
            status_code=403,
            detail="Tier updates require coordinator role",
        )

    new_tier = request.tier
    if new_tier not in VALID_TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid tier {new_tier!r}. Must be one of: {sorted(VALID_TIERS)}",
        )

    db = _get_db()
    if not hasattr(db, "update_engram_tier"):
        raise HTTPException(status_code=501, detail="update_engram_tier not supported by this backend")

    updated = db.update_engram_tier(engram_id, new_tier)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Engram {engram_id!r} not found")

    logger.info(
        "Tier updated: engram=%s new_tier=%s by %s",
        engram_id, new_tier, ctx.owner_id or "admin",
    )
    return {"status": "updated", "engram_id": engram_id, "tier": new_tier}
