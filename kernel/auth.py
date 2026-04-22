"""
Mirror auth — TokenContext and resolve_token_context.

Replaces the inline resolve_token() in mirror_api.py with a richer
context object that carries workspace_id, owner_type, and owner_id.
This is the single source of truth for all auth decisions in Mirror.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("mirror.auth")

_FALLBACK_ADMIN_TOKEN = "sk-mumega-internal-001"
_FALLBACK_TENANT_KEYS_PATH = "/home/mumega/mirror/tenant_keys.json"


@dataclass
class TokenContext:
    """Resolved identity from a Bearer token.

    Attributes:
        workspace_id: Hard isolation boundary. None means admin (sees all).
        owner_type:   'user' | 'project' | 'squad' | 'agent' | None (admin).
        owner_id:     Identifier of the owner within the workspace.
        is_admin:     True only for the internal admin token.
    """

    workspace_id: Optional[str]
    owner_type: Optional[str]
    owner_id: Optional[str]
    is_admin: bool = field(default=False)


def _load_tenant_keys(path: str) -> dict[str, dict]:
    """Load tenant_keys.json → {key_hash: entry_dict}."""
    try:
        with open(path) as f:
            raw = json.load(f)
        items = raw if isinstance(raw, list) else [raw]
        return {
            hashlib.sha256(item["key"].encode()).hexdigest(): item
            for item in items
            if item.get("active")
        }
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to load tenant keys from %s: %s", path, exc)
        return {}


def resolve_token_context(
    authorization: str,
    *,
    admin_token: str = None,
    tenant_keys_path: str = None,
) -> TokenContext:
    """Validate a Bearer token and return a TokenContext.

    Resolution order:
    1. Empty → 401
    2. Admin token → TokenContext(is_admin=True)
    3. tenant_keys.json hit → TokenContext scoped to that tenant
    4. SOS bus token (sos.services.auth) → TokenContext scoped to project
    5. Unknown → 401

    Args:
        authorization: Full "Bearer <token>" header value (or bare token).
        admin_token:   Override for testing.
        tenant_keys_path: Override for testing.
    """
    if admin_token is None:
        admin_token = os.getenv("MIRROR_ADMIN_TOKEN", _FALLBACK_ADMIN_TOKEN)
    if tenant_keys_path is None:
        tenant_keys_path = os.getenv("MIRROR_TENANT_KEYS_PATH", _FALLBACK_TENANT_KEYS_PATH)

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required")

    # 1. Admin
    if token == admin_token:
        return TokenContext(workspace_id=None, owner_type=None, owner_id=None, is_admin=True)

    # 2. Tenant keys
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    keys = _load_tenant_keys(tenant_keys_path)
    if key_hash in keys:
        entry = keys[key_hash]
        slug = entry["agent_slug"]
        workspace_id = entry.get("workspace_id") or slug
        return TokenContext(
            workspace_id=workspace_id,
            owner_type="agent",
            owner_id=slug,
            is_admin=False,
        )

    # 3. SOS bus tokens — primary path for all SOS agents
    try:
        import sys as _sys
        if '/home/mumega/SOS' not in _sys.path:
            _sys.path.insert(0, '/home/mumega/SOS')
        from sos.kernel.auth import verify_bearer as _sos_verify  # type: ignore[import]
        sos_ctx = _sos_verify(f"Bearer {token}")
        if sos_ctx is not None:
            # Prefer agent identity; fall back to project scope
            owner_id = sos_ctx.agent or sos_ctx.project or "unknown"
            workspace_id = sos_ctx.project or "sos"
            return TokenContext(
                workspace_id=workspace_id,
                owner_type="agent",
                owner_id=owner_id,
                is_admin=getattr(sos_ctx, 'is_admin', False),
            )
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("SOS auth check failed: %s", exc)

    raise HTTPException(status_code=401, detail="Invalid token")
