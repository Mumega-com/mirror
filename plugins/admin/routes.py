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

logger = logging.getLogger("mirror.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


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
