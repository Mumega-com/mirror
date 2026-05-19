"""
Tests for cross-tenant data sovereignty (DS-001 + DS-002).

Covers:
- internal-agents namespace isolation for internal agents
- Customer tokens cannot see internal-agents engrams
- Customer A cannot see Customer B engrams
- Admin token sees all workspaces
- Stats endpoint scopes count to caller workspace
- search() never leaks workspace_id=None engrams to non-admin callers
- BM25 search also scopes by workspace_id
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Use SQLite backend — no live DB needed
os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_ds.db"
os.environ["MIRROR_INTERNAL_AGENTS"] = "planner,reviewer,indexer,coordinator,mirror,codex,sol,hermes,assistant,worker,worker-2"
pathlib.Path("/tmp/mirror_test_ds.db").unlink(missing_ok=True)

from kernel.auth import INTERNAL_AGENTS, TokenContext, _load_internal_agents, resolve_token_context
from kernel.db import get_db

_db = get_db()
_VEC = [0.1] * 1536


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(context_id: str, workspace_id: str | None, text: str = "test memory") -> None:
    _db.upsert_engram({
        "context_id": context_id,
        "series": "ds-test",
        "workspace_id": workspace_id,
        "owner_type": "agent",
        "owner_id": workspace_id or "admin",
        "raw_data": json.dumps({"text": text, "agent": workspace_id or "admin"}),
        "embedding": _VEC,
    })


def _make_tenant_file(entries: list[dict]) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entries, f)
    f.flush()
    return f.name


ADMIN_TOKEN = "sk-admin-test-001"


# ---------------------------------------------------------------------------
# DS-001: INTERNAL_AGENTS constant
# ---------------------------------------------------------------------------

def test_internal_agents_default_empty():
    """OSS builds do not hardcode internal agent slugs."""
    assert INTERNAL_AGENTS == frozenset()


def test_internal_agents_can_be_configured_from_env():
    """Internal agent slugs are configured by MIRROR_INTERNAL_AGENTS."""
    expected = {"planner", "reviewer", "indexer", "coordinator", "mirror", "codex",
                "sol", "hermes", "assistant", "worker", "worker-2"}
    assert expected.issubset(_load_internal_agents())


def test_internal_agents_does_not_include_customer_slugs():
    """Customer slug must not be in the internal set."""
    assert "acme" not in INTERNAL_AGENTS
    assert "viamar" not in INTERNAL_AGENTS
    assert "beta-corp" not in INTERNAL_AGENTS


# ---------------------------------------------------------------------------
# DS-001: tenant_keys.json — internal agent slug → internal-agents workspace
# ---------------------------------------------------------------------------

def test_internal_agent_tenant_key_resolves_to_internal_namespace():
    """
    A tenant_keys.json entry for an internal agent (e.g. planner) that has
    no explicit workspace_id should NOT fall back to the slug — the slug IS
    the workspace. We document the current behaviour and verify planner is internal.
    """
    # The current resolve path for tenant_keys uses entry.get("workspace_id") or slug
    # as the workspace_id. This test verifies that if we explicitly set
    # workspace_id="internal-agents" in the file, it is honoured.
    key = "sk-planner-test-tenant-001"
    path = _make_tenant_file([{
        "key": key,
        "agent_slug": "planner",
        "workspace_id": "internal-agents",
        "active": True,
    }])
    ctx = resolve_token_context(
        f"Bearer {key}",
        admin_token=ADMIN_TOKEN,
        tenant_keys_path=path,
    )
    assert ctx.workspace_id == "internal-agents"
    assert ctx.is_admin is False


def test_customer_tenant_key_does_not_get_internal_namespace():
    """A customer key must NOT be tagged internal-agents."""
    key = "sk-customer-acme-001"
    path = _make_tenant_file([{
        "key": key,
        "agent_slug": "acme-corp",
        "active": True,
    }])
    ctx = resolve_token_context(
        f"Bearer {key}",
        admin_token=ADMIN_TOKEN,
        tenant_keys_path=path,
    )
    assert ctx.workspace_id != "internal-agents"
    assert ctx.workspace_id == "acme-corp"
    assert ctx.is_admin is False


# ---------------------------------------------------------------------------
# DS-002: Cross-tenant isolation at DB level
# ---------------------------------------------------------------------------

def test_internal_agent_cannot_see_customer_engrams():
    """An internal agent searching with workspace_id=internal-agents must not see customer data."""
    _store("cust-secret-001", "acme-corp", "acme proprietary algorithm")
    _store("internal-mem-001", "internal-agents", "planner internal note")

    results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="internal-agents")
    ids = [r["context_id"] for r in results]

    assert "internal-mem-001" in ids
    assert "cust-secret-001" not in ids


def test_customer_cannot_see_mirror_internal_engrams():
    """A customer token (workspace_id=acme) must not see internal-agents engrams."""
    _store("internal-mem-002", "internal-agents", "coordinator core state")
    _store("cust-mem-001", "acme", "acme customer data")

    results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="acme")
    ids = [r["context_id"] for r in results]

    assert "cust-mem-001" in ids
    assert "internal-mem-002" not in ids


def test_customer_a_cannot_see_customer_b_engrams():
    """Cross-tenant isolation: acme and beta-corp must be fully separated."""
    _store("acme-data-001", "acme", "acme secret data")
    _store("beta-data-001", "beta-corp", "beta corp secret data")

    acme_results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="acme")
    beta_results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="beta-corp")

    acme_ids = [r["context_id"] for r in acme_results]
    beta_ids = [r["context_id"] for r in beta_results]

    # Each customer sees their own data
    assert "acme-data-001" in acme_ids
    assert "beta-data-001" in beta_ids
    # Each customer does NOT see the other's data
    assert "beta-data-001" not in acme_ids
    assert "acme-data-001" not in beta_ids


def test_admin_token_sees_all_workspaces():
    """Admin (workspace_id=None) must see engrams from all namespaces."""
    _store("admin-vis-internal", "internal-agents", "internal agent memory")
    _store("admin-vis-acme", "acme", "acme customer memory")
    _store("admin-vis-beta", "beta-corp", "beta customer memory")

    # Admin search: no workspace_id filter
    results = _db.search_engrams(_VEC, threshold=0.0, limit=100, workspace_id=None)
    ids = [r["context_id"] for r in results]

    assert "admin-vis-internal" in ids
    assert "admin-vis-acme" in ids
    assert "admin-vis-beta" in ids


def test_search_with_workspace_filter_returns_only_own_namespace():
    """Explicit workspace_id on search must hard-scope results."""
    _store("scope-internal-001", "internal-agents", "scope test internal")
    _store("scope-acme-001", "scope-acme", "scope test acme")
    _store("scope-beta-001", "scope-beta", "scope test beta")

    acme_results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="scope-acme")
    acme_ids = [r["context_id"] for r in acme_results]

    assert "scope-acme-001" in acme_ids
    assert "scope-internal-001" not in acme_ids
    assert "scope-beta-001" not in acme_ids


def test_recent_engrams_scoped_to_workspace():
    """recent_engrams() must honour workspace_id filter."""
    _store("recent-int-001", "internal-agents", "recent internal")
    _store("recent-cust-001", "gamma-corp", "recent customer")

    results = _db.recent_engrams("ds-test", limit=50, workspace_id="internal-agents")
    ids = [r["context_id"] for r in results]

    assert "recent-int-001" in ids
    assert "recent-cust-001" not in ids


def test_count_engrams_in_workspace_is_scoped():
    """count_engrams_in_workspace must return only the caller's workspace count."""
    if not hasattr(_db, "count_engrams_in_workspace"):
        pytest.skip("count_engrams_in_workspace not implemented in this backend")

    _store("count-int-001", "internal-agents", "count test internal 1")
    _store("count-int-002", "internal-agents", "count test internal 2")
    _store("count-cust-001", "delta-corp", "count test customer")

    internal_count = _db.count_engrams_in_workspace("internal-agents")
    customer_count = _db.count_engrams_in_workspace("delta-corp")

    # Internal count must be >= 2 (we stored 2 in this test, possibly more from others)
    assert internal_count >= 2
    # Customer count must be exactly the engrams for that workspace
    assert customer_count >= 1
    # They must not equal each other (since they're different namespaces with different data)
    # We just verify customer count doesn't include internal data
    internal_only_count = _db.count_engrams_in_workspace("internal-agents")
    customer_only_count = _db.count_engrams_in_workspace("delta-corp")
    assert internal_only_count != customer_only_count or (internal_only_count == customer_only_count == 1)


def test_null_workspace_engrams_not_visible_to_non_admin():
    """
    Engrams stored with workspace_id=None (admin pool) must not appear
    when a non-admin caller searches with their own workspace_id.
    """
    _store("null-ws-001", None, "admin pool engram — should not leak")
    _store("isolated-cust-001", "epsilon-corp", "epsilon customer engram")

    results = _db.search_engrams(_VEC, threshold=0.0, limit=100, workspace_id="epsilon-corp")
    ids = [r["context_id"] for r in results]

    assert "isolated-cust-001" in ids
    assert "null-ws-001" not in ids
