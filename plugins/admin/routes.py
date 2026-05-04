"""
Admin plugin routes — workspace and token management.

All endpoints require the admin token (is_admin=True on TokenContext).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from kernel.auth import TokenContext, resolve_token_context
from kernel.db import get_db
from kernel.outbox import DEFAULT_QUEUE, is_outbox_enabled, make_outbox

logger = logging.getLogger("mirror.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

# WARN-F16-005 — outbox admin endpoints accept a ?queue= param. Restrict it to
# a known allowlist so callers cannot poke arbitrary queue names against the
# stats/DLQ surface. Today there is exactly one queue ("inkwell-receipts");
# adding a queue means adding it here.
ALLOWED_OUTBOX_QUEUES = frozenset({DEFAULT_QUEUE})


def _check_outbox_queue(queue: str) -> None:
    if queue not in ALLOWED_OUTBOX_QUEUES:
        raise HTTPException(status_code=422, detail="queue_not_allowed")


def _get_admin(authorization: str = Header(default="")) -> TokenContext:
    ctx = resolve_token_context(authorization)
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Admin token required")
    return ctx


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateWorkspaceRequest(BaseModel):
    slug: str
    name: str


class IssueTokenRequest(BaseModel):
    label: str
    token_type: str = "agent"   # agent | squad | readonly | admin
    owner_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Workspace endpoints
# ---------------------------------------------------------------------------

@router.post("/workspaces")
def create_workspace(
    request: CreateWorkspaceRequest,
    ctx: TokenContext = Depends(_get_admin),
):
    """Create a new workspace."""
    try:
        db = get_db()
        if not hasattr(db, "create_workspace"):
            raise HTTPException(status_code=501, detail="Token DB not available on this backend")
        workspace = db.create_workspace(slug=request.slug, name=request.name)
        logger.info("Created workspace: %s (%s)", request.slug, workspace["id"])
        return workspace
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/workspaces")
def list_workspaces(ctx: TokenContext = Depends(_get_admin)):
    """List all active workspaces."""
    db = get_db()
    if not hasattr(db, "list_workspaces"):
        raise HTTPException(status_code=501, detail="Token DB not available on this backend")
    return {"workspaces": db.list_workspaces()}


# ---------------------------------------------------------------------------
# Token endpoints
# ---------------------------------------------------------------------------

@router.post("/workspaces/{workspace_id}/tokens")
def issue_token(
    workspace_id: str,
    request: IssueTokenRequest,
    ctx: TokenContext = Depends(_get_admin),
):
    """Issue a new token for a workspace. Returns plaintext token once — store it."""
    valid_types = {"agent", "squad", "readonly", "admin"}
    if request.token_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"token_type must be one of: {valid_types}")
    try:
        db = get_db()
        if not hasattr(db, "issue_token"):
            raise HTTPException(status_code=501, detail="Token DB not available on this backend")
        result = db.issue_token(
            workspace_id=workspace_id,
            label=request.label,
            token_type=request.token_type,
            owner_id=request.owner_id,
        )
        logger.info("Issued token %s for workspace %s", result["token_id"], workspace_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/workspaces/{workspace_id}/tokens")
def list_tokens(
    workspace_id: str,
    ctx: TokenContext = Depends(_get_admin),
):
    """List active tokens for a workspace (hashes not included)."""
    db = get_db()
    if not hasattr(db, "list_tokens"):
        raise HTTPException(status_code=501, detail="Token DB not available on this backend")
    return {"workspace_id": workspace_id, "tokens": db.list_tokens(workspace_id)}


# ---------------------------------------------------------------------------
# Outbox status (S024 F-16) — feeds substrate-monitor outbox.status (F-17)
# ---------------------------------------------------------------------------

@router.get("/outbox/status")
def outbox_status(
    queue: str = DEFAULT_QUEUE,
    ctx: TokenContext = Depends(_get_admin),
):
    """Return outbox queue depth + DLQ count (S024 F-16/F-17 contract).

    Shape (matches v0.5 brief §6.6 F-17 component schema):
      {
        "queue": "inkwell-receipts",
        "backend": "native" | "memory" | "disabled" | "unknown",
        "enabled": bool,
        "pending_count": N,
        "in_flight_count": N,
        "dlq_count": N,
      }
    """
    _check_outbox_queue(queue)
    db = get_db()
    enabled = is_outbox_enabled()
    if not enabled:
        return {
            "queue": queue,
            "backend": "disabled",
            "enabled": False,
            "pending_count": 0,
            "in_flight_count": 0,
            "dlq_count": 0,
        }
    outbox = make_outbox(db)
    backend_name = type(outbox).__name__
    backend = (
        "native" if backend_name == "NativeSqlOutbox"
        else "memory" if backend_name == "MemoryOutbox"
        else "unknown"
    )
    try:
        stats = outbox.stats(queue=queue)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"outbox stats error: {exc}")
    return {
        "queue": queue,
        "backend": backend,
        "enabled": True,
        "pending_count": int(stats.get("pending", 0)),
        "in_flight_count": int(stats.get("in_flight", 0)),
        "dlq_count": int(stats.get("dlq", 0)),
    }


@router.get("/outbox/dlq")
def outbox_dlq(
    queue: str = DEFAULT_QUEUE,
    limit: int = 10,
    ctx: TokenContext = Depends(_get_admin),
):
    """Inspect up to *limit* DLQ rows (operator surface, S024 F-16)."""
    _check_outbox_queue(queue)
    db = get_db()
    if not is_outbox_enabled():
        return {"queue": queue, "rows": []}
    outbox = make_outbox(db)
    try:
        rows = outbox.dlq_inspect(queue=queue, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"outbox dlq_inspect error: {exc}")
    return {"queue": queue, "rows": rows}


@router.delete("/workspaces/{workspace_id}/tokens/{token_id}")
def revoke_token(
    workspace_id: str,
    token_id: str,
    ctx: TokenContext = Depends(_get_admin),
):
    """Revoke a token (soft delete — sets active=false)."""
    db = get_db()
    if not hasattr(db, "revoke_token"):
        raise HTTPException(status_code=501, detail="Token DB not available on this backend")
    db.revoke_token(token_id)
    logger.info("Revoked token %s from workspace %s", token_id, workspace_id)
    return {"status": "revoked", "token_id": token_id}
