"""Tests for kernel.auth — TokenContext + resolve_token_context."""
import hashlib
import json
import os
import tempfile
import pytest

# Point at local mirror package
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from kernel.auth import TokenContext, resolve_token_context


ADMIN_TOKEN = "sk-mumega-internal-001"


def _make_tenant_file(entries: list[dict]) -> str:
    """Write a tenant_keys.json to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entries, f)
    f.flush()
    return f.name


# ---------------------------------------------------------------------------
# TokenContext shape
# ---------------------------------------------------------------------------

def test_token_context_is_admin_false_by_default():
    ctx = TokenContext(workspace_id="ws-1", owner_type="user", owner_id="u-1")
    assert ctx.is_admin is False


def test_token_context_admin_has_no_workspace():
    ctx = TokenContext(workspace_id=None, owner_type=None, owner_id=None, is_admin=True)
    assert ctx.workspace_id is None
    assert ctx.is_admin is True


# ---------------------------------------------------------------------------
# Admin token
# ---------------------------------------------------------------------------

def test_admin_token_returns_admin_context(tmp_path):
    ctx = resolve_token_context(
        f"Bearer {ADMIN_TOKEN}",
        admin_token=ADMIN_TOKEN,
        tenant_keys_path=str(tmp_path / "empty.json"),
    )
    assert ctx.is_admin is True
    assert ctx.workspace_id is None


def test_admin_token_has_no_builtin_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MIRROR_ADMIN_TOKEN", raising=False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_token_context(
            f"Bearer {ADMIN_TOKEN}",
            tenant_keys_path=str(tmp_path / "empty.json"),
        )
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Tenant (tenant_keys.json) tokens
# ---------------------------------------------------------------------------

def test_tenant_token_returns_scoped_context():
    key = "sk-tenant-abc123"
    path = _make_tenant_file([
        {"key": key, "agent_slug": "viamar", "active": True}
    ])
    ctx = resolve_token_context(
        f"Bearer {key}",
        admin_token=ADMIN_TOKEN,
        tenant_keys_path=path,
    )
    assert ctx.workspace_id == "viamar"
    assert ctx.owner_type == "agent"
    assert ctx.owner_id == "viamar"
    assert ctx.is_admin is False


def test_inactive_tenant_key_raises_401():
    key = "sk-tenant-inactive"
    path = _make_tenant_file([
        {"key": key, "agent_slug": "viamar", "active": False}
    ])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_token_context(
            f"Bearer {key}",
            admin_token=ADMIN_TOKEN,
            tenant_keys_path=path,
        )
    assert exc_info.value.status_code in (401, 403)


def test_missing_token_raises_401(tmp_path):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_token_context(
            "",
            admin_token=ADMIN_TOKEN,
            tenant_keys_path=str(tmp_path / "empty.json"),
        )
    assert exc_info.value.status_code == 401


def test_unknown_token_raises_401(tmp_path):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_token_context(
            "Bearer sk-totally-unknown",
            admin_token=ADMIN_TOKEN,
            tenant_keys_path=str(tmp_path / "empty.json"),
        )
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Workspace field on tenant keys (optional extension)
# ---------------------------------------------------------------------------

def test_tenant_key_with_explicit_workspace():
    key = "sk-tenant-ws"
    path = _make_tenant_file([{
        "key": key,
        "agent_slug": "viamar",
        "workspace_id": "ws-custom-123",
        "active": True,
    }])
    ctx = resolve_token_context(
        f"Bearer {key}",
        admin_token=ADMIN_TOKEN,
        tenant_keys_path=path,
    )
    assert ctx.workspace_id == "ws-custom-123"
    assert ctx.owner_id == "viamar"


def test_s027_mirror_keys_path_is_legacy_fallback(monkeypatch, tmp_path):
    key = "sk-mumega-acme-s027"
    primary = tmp_path / "missing-tenant-keys.json"
    s027 = tmp_path / "mirror_keys.json"
    s027.write_text(json.dumps([{
        "key": key,
        "agent_slug": "acme",
        "active": True,
        "label": "Acme mirror access",
    }]))

    monkeypatch.setenv("MIRROR_TENANT_KEYS_PATH", str(primary))
    monkeypatch.setenv("MIRROR_SOS_MIRROR_KEYS_PATH", str(s027))

    ctx = resolve_token_context(
        f"Bearer {key}",
        admin_token=ADMIN_TOKEN,
    )

    assert ctx.workspace_id == "acme"
    assert ctx.owner_type == "agent"
    assert ctx.owner_id == "acme"
    assert ctx.is_admin is False


def test_db_issued_delivery_cache_is_not_legacy_auth_fallback(monkeypatch, tmp_path):
    key = "sk-acme-db-issued-cache-only"
    primary = tmp_path / "missing-tenant-keys.json"
    s027 = tmp_path / "mirror_keys.json"
    s027.write_text(json.dumps([{
        "key": key,
        "agent_slug": "acme",
        "active": True,
        "label": "Acme mirror access",
        "source": "mirror_tokens",
        "mirror_workspace_id": "ws-acme",
        "mirror_token_id": "tok-acme",
    }]))

    monkeypatch.setenv("MIRROR_TENANT_KEYS_PATH", str(primary))
    monkeypatch.setenv("MIRROR_SOS_MIRROR_KEYS_PATH", str(s027))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_token_context(
            f"Bearer {key}",
            admin_token=ADMIN_TOKEN,
        )

    assert exc_info.value.status_code == 401


def test_legacy_mirror_api_resolve_token_delegates_to_kernel_auth(monkeypatch):
    import mirror_api

    calls = []

    def fake_resolve(authorization):
        calls.append(authorization)
        return TokenContext(
            workspace_id="ws-acme",
            owner_type="agent",
            owner_id="acme",
        )

    monkeypatch.setattr("kernel.auth.resolve_token_context", fake_resolve)

    assert mirror_api.resolve_token("Bearer sk-acme") == "ws-acme"
    assert calls == ["Bearer sk-acme"]


def test_legacy_mirror_api_resolve_token_admin_returns_unscoped(monkeypatch):
    import mirror_api

    def fake_resolve(authorization):
        return TokenContext(
            workspace_id=None,
            owner_type=None,
            owner_id=None,
            is_admin=True,
        )

    monkeypatch.setattr("kernel.auth.resolve_token_context", fake_resolve)

    assert mirror_api.resolve_token("Bearer sk-admin") is None
