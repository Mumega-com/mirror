"""
Mirror auth — TokenContext and resolve_token_context.

Replaces the inline resolve_token() in mirror_api.py with a richer
context object that carries workspace_id, owner_type, and owner_id.
This is the single source of truth for all auth decisions in Mirror.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("mirror.auth")

_FALLBACK_TENANT_KEYS_PATH = "tenant_keys.json"

def _load_internal_agents() -> frozenset[str]:
    """Return optional internally trusted agent slugs from env."""
    raw = os.getenv("MIRROR_INTERNAL_AGENTS", "")
    return frozenset(slug.strip().lower() for slug in raw.split(",") if slug.strip())


INTERNAL_AGENTS: frozenset[str] = _load_internal_agents()


VALID_TIERS: frozenset[str] = frozenset({"public", "squad", "project", "entity", "private"})


@dataclass
class TokenContext:
    """Resolved identity from a Bearer token.

    Attributes:
        workspace_id:  Hard isolation boundary. None means admin (sees all).
        owner_type:    'user' | 'project' | 'squad' | 'agent' | None (admin).
        owner_id:      Identifier of the owner within the workspace.
        is_admin:      True only for the internal admin token.
        tier_access:   Tiers this caller can read. Default: ['public', 'project'].
        entity_id:     Entity identifier for entity-scoped engrams (matches engram.entity_id).
        role:          Caller's role — 'coordinator' grants tier promotion rights.
        project_id:    Trusted project binding. Never taken from a request body.
        principal_id:  Trusted principal. Never taken from a caller-supplied agent name.
    """

    workspace_id: Optional[str]
    owner_type: Optional[str]
    owner_id: Optional[str]
    is_admin: bool = field(default=False)
    tier_access: list[str] = field(default_factory=lambda: ["public", "project"])
    entity_id: Optional[str] = field(default=None)
    role: Optional[str] = field(default=None)
    project_id: Optional[str] = field(default=None)
    principal_id: Optional[str] = field(default=None)


def _load_tenant_keys(path: str) -> dict[str, dict]:
    """Load tenant_keys.json → {key_hash: entry_dict}."""
    try:
        with open(path) as f:
            raw = json.load(f)
        items = raw if isinstance(raw, list) else [raw]
        return {
            hashlib.sha256(item["key"].encode()).hexdigest(): item
            for item in items
            if item.get("active") and item.get("source", "legacy_file") != "mirror_tokens"
        }
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to load tenant keys from %s: %s", path, exc)
        return {}


def _load_legacy_key_paths(paths: list[str]) -> dict[str, dict]:
    """Load legacy file-backed Mirror keys from first-class + S027 paths."""
    keys: dict[str, dict] = {}
    for path in paths:
        if not path:
            continue
        keys.update(_load_tenant_keys(path))
    return keys


def resolve_token_context(
    authorization: str,
    *,
    admin_token: str = None,
    tenant_keys_path: str = None,
) -> TokenContext:
    """Validate a Bearer token and return a TokenContext.

    Resolution order:
    1. Empty → 401
    2. Admin token → TokenContext(is_admin=True, workspace_id=None)
    3. DB-backed token (mirror_tokens table) → scoped to workspace
    4. legacy key-file hit (tenant_keys.json) → TokenContext scoped to that tenant
    5. Unknown → 401

    Args:
        authorization: Full "Bearer <token>" header value (or bare token).
        admin_token:   Override for testing.
        tenant_keys_path: Override for testing.
    """
    if admin_token is None:
        admin_token = os.getenv("MIRROR_ADMIN_TOKEN", "")
    if tenant_keys_path is None:
        tenant_key_paths = [
            os.getenv("MIRROR_TENANT_KEYS_PATH", _FALLBACK_TENANT_KEYS_PATH),
        ]
    else:
        tenant_key_paths = [tenant_keys_path]

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required")

    # 1. Admin
    # Constant-time comparison — F-16 elevated this from rare-call to
    # remote-reachable surface (/admin/outbox/status, /admin/outbox/dlq).
    # Adversarial-gate hardening BLOCK-P1-6.
    if hmac.compare_digest(token or "", admin_token or ""):
        return TokenContext(
            workspace_id=None,
            owner_type=None,
            owner_id=None,
            is_admin=True,
            tier_access=list(VALID_TIERS),
            role="coordinator",
        )

    key_hash = hashlib.sha256(token.encode()).hexdigest()

    # 2.5 DB-backed tokens (mirror_tokens table) — primary path for issued tokens
    try:
        from kernel.db import get_db as _get_db
        _db = _get_db()
        if hasattr(_db, "resolve_token_from_db"):
            row = _db.resolve_token_from_db(key_hash)
            if row:
                _tier_access = list(row.get("tier_access") or ["public", "project"])
                _entity_id = row.get("entity_id") or row.get("workspace_id")
                return TokenContext(
                    workspace_id=row["workspace_id"],
                    owner_type=row["token_type"],
                    owner_id=row.get("owner_id") or row.get("label"),
                    is_admin=False,
                    tier_access=_tier_access,
                    entity_id=_entity_id,
                    role=row.get("role"),
                    project_id=row.get("project_id"),
                    principal_id=row.get("principal_id") or row.get("owner_id") or row.get("label"),
                )
    except Exception as _exc:
        logger.warning("DB token lookup failed: %s", _exc)

    # 3. Tenant keys (legacy — tenant_keys.json fallback)
    keys = _load_legacy_key_paths(tenant_key_paths)
    if key_hash in keys:
        entry = keys[key_hash]
        slug = entry["agent_slug"]
        workspace_id = entry.get("workspace_id") or slug
        _tier_access = list(entry.get("tier_access") or ["public", "project"])
        _entity_id = entry.get("entity_id") or workspace_id
        _role = entry.get("role")
        return TokenContext(
            workspace_id=workspace_id,
            owner_type="agent",
            owner_id=slug,
            is_admin=False,
            tier_access=_tier_access,
            entity_id=_entity_id,
            role=_role,
            project_id=entry.get("project_id"),
            principal_id=entry.get("principal_id") or slug,
        )

    raise HTTPException(status_code=401, detail="Invalid token")
