"""Admin-token hardening for workspace token issuance routes."""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from kernel.auth import TokenContext
from plugins.admin import routes


def test_admin_guard_rejects_workspace_scoped_admin_context(monkeypatch):
    monkeypatch.setattr(
        routes,
        "resolve_token_context",
        lambda authorization: TokenContext(
            workspace_id="ws-tenant",
            owner_type="admin",
            owner_id="tenant-admin",
            is_admin=True,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        routes._get_admin("Bearer sk-tenant-admin")

    assert exc_info.value.status_code == 403


def test_admin_guard_accepts_root_admin_context(monkeypatch):
    monkeypatch.setattr(
        routes,
        "resolve_token_context",
        lambda authorization: TokenContext(
            workspace_id=None,
            owner_type=None,
            owner_id=None,
            is_admin=True,
        ),
    )

    ctx = routes._get_admin("Bearer sk-root-admin")

    assert ctx.is_admin is True
    assert ctx.workspace_id is None


def test_issue_token_route_rejects_admin_token_type():
    ctx = TokenContext(
        workspace_id=None,
        owner_type=None,
        owner_id=None,
        is_admin=True,
    )

    with pytest.raises(HTTPException) as exc_info:
        routes.issue_token(
            "ws-tenant",
            routes.IssueTokenRequest(label="bad", token_type="admin"),
            ctx,
        )

    assert exc_info.value.status_code == 400


def test_revoke_token_route_requires_workspace_match(monkeypatch):
    class FakeDB:
        def revoke_token(self, token_id, workspace_id):
            assert token_id == "tok-a"
            assert workspace_id == "ws-a"
            return False

    monkeypatch.setattr(routes, "get_db", lambda: FakeDB())
    ctx = TokenContext(
        workspace_id=None,
        owner_type=None,
        owner_id=None,
        is_admin=True,
    )

    with pytest.raises(HTTPException) as exc_info:
        routes.revoke_token("ws-a", "tok-a", ctx)

    assert exc_info.value.status_code == 404
