"""
Tests for Section 1C: five-tier engram access model.

Covers:
- Migration smoke: after migration, all pre-existing engrams have tier='project'
- RBAC leak: entity-A token returns 0 results for entity-B engrams in same workspace
- Cross-tier: public engrams visible to all; private engrams only to creator
- PATCH /engrams/{id}/tier by coordinator promotes tier; subsequent recall returns it
- Default tier='project' when not specified in store request
- Invalid tier rejected at store and PATCH
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Use SQLite backend — no live DB needed
os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_tiers.db"
pathlib.Path("/tmp/mirror_test_tiers.db").unlink(missing_ok=True)

from kernel.db import get_db

_db = get_db()

_VEC = [0.1] * 1536
_VEC_PRIVATE = [0.9] * 1536  # distinct vector so we can isolate it


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(
    context_id: str,
    workspace_id: str,
    tier: str = "project",
    entity_id: str = None,
    owner_id: str = None,
    vec=None,
) -> None:
    _db.upsert_engram({
        "context_id": context_id,
        "series": "tier-test",
        "workspace_id": workspace_id,
        "owner_type": "agent",
        "owner_id": owner_id or workspace_id,
        "raw_data": {"text": f"engram {context_id}", "agent": "test"},
        "embedding": vec if vec is not None else _VEC,
        "tier": tier,
        "entity_id": entity_id or workspace_id,
    })


def _ids(results: list[dict]) -> list[str]:
    return [r["context_id"] for r in results]


# ---------------------------------------------------------------------------
# 1. Migration smoke: pre-existing engrams default to tier='project'
# ---------------------------------------------------------------------------

def test_default_tier_is_project():
    """Engrams stored without explicit tier must have tier='project'."""
    _db.upsert_engram({
        "context_id": "tier-default-001",
        "series": "tier-test",
        "workspace_id": "ws-default",
        "owner_type": "agent",
        "owner_id": "ws-default",
        "raw_data": {"text": "no explicit tier", "agent": "test"},
        "embedding": _VEC,
        # No 'tier' key — should default to 'project'
    })
    results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="ws-default")
    match = next((r for r in results if r["context_id"] == "tier-default-001"), None)
    assert match is not None, "Engram not found"
    assert match["tier"] == "project", f"Expected tier='project', got {match['tier']!r}"


def test_migration_smoke_all_existing_engrams_have_tier_project():
    """After init, all engrams that were stored without tier must have tier='project' (not NULL)."""
    # Store several engrams without tier (simulating pre-migration state would require
    # direct SQL; instead we verify the schema default works via the public API)
    for i in range(3):
        _db.upsert_engram({
            "context_id": f"smoke-{i}",
            "series": "smoke-test",
            "workspace_id": "ws-smoke",
            "owner_type": "agent",
            "owner_id": "ws-smoke",
            "raw_data": {"text": f"smoke {i}", "agent": "test"},
            "embedding": _VEC,
        })

    results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="ws-smoke")
    smoke_results = [r for r in results if r["context_id"].startswith("smoke-")]
    assert len(smoke_results) >= 3
    for r in smoke_results:
        assert r.get("tier") is not None, f"tier is NULL for engram {r['context_id']}"
        assert r["tier"] == "project", f"Expected 'project', got {r['tier']!r}"


# ---------------------------------------------------------------------------
# 2. RBAC leak: entity-A token cannot see entity-B engrams
# ---------------------------------------------------------------------------

def test_entity_a_cannot_see_entity_b_engrams():
    """Entity-scoped engrams (tier='entity') must not leak across entity boundaries."""
    _store("entity-a-secret", "shared-ws", tier="entity", entity_id="entity-a")
    _store("entity-b-secret", "shared-ws", tier="entity", entity_id="entity-b")

    # Entity A search — should only see entity-a engrams
    results_a = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="shared-ws",
        tier_access=["entity", "project"],
        caller_entity_id="entity-a",
    )
    ids_a = _ids(results_a)
    assert "entity-a-secret" in ids_a, "entity-a should see its own entity-scoped engram"
    assert "entity-b-secret" not in ids_a, "entity-a must NOT see entity-b engrams"


def test_entity_b_cannot_see_entity_a_engrams():
    """Symmetric check for entity-B."""
    _store("rbac-a-001", "ws-rbac", tier="entity", entity_id="rbac-entity-a")
    _store("rbac-b-001", "ws-rbac", tier="entity", entity_id="rbac-entity-b")

    results_b = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-rbac",
        tier_access=["entity", "project"],
        caller_entity_id="rbac-entity-b",
    )
    ids_b = _ids(results_b)
    assert "rbac-b-001" in ids_b
    assert "rbac-a-001" not in ids_b


def test_entity_scoped_engrams_zero_results_without_entity_id():
    """Caller with tier_access=['entity'] but no caller_entity_id should not see entity engrams."""
    _store("entity-only-001", "ws-entity-only", tier="entity", entity_id="secret-entity")

    # Caller has entity in tier_access but no entity_id — entity tier requires explicit match
    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-entity-only",
        tier_access=["entity", "project"],
        caller_entity_id=None,  # No entity_id
    )
    ids = _ids(results)
    assert "entity-only-001" not in ids, "Entity-scoped engrams must not leak without entity_id"


# ---------------------------------------------------------------------------
# 3. Cross-tier: public visible to all; private only to creator
# ---------------------------------------------------------------------------

def test_public_engrams_visible_to_any_tier_access():
    """Public engrams must be visible regardless of what tier_access the caller has."""
    _store("public-001", "ws-public", tier="public", entity_id="ws-public")

    # Caller with only project tier_access should see public engrams
    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-public",
        tier_access=["project"],
        caller_entity_id="ws-public",
    )
    ids = _ids(results)
    assert "public-001" in ids, "Public engrams must be visible to any valid caller"


def test_public_engrams_visible_even_with_minimal_tier_access():
    """Public engrams visible even when tier_access is an empty list."""
    _store("public-minimal-001", "ws-pub-min", tier="public")

    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-pub-min",
        tier_access=[],  # empty — means only public
        caller_entity_id="ws-pub-min",
    )
    ids = _ids(results)
    assert "public-minimal-001" in ids


def test_private_tier_not_visible_without_tier_access():
    """Private engrams must not appear if 'private' is not in caller's tier_access."""
    _store("private-001", "ws-private", tier="private", entity_id="ws-private")

    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-private",
        tier_access=["public", "project"],  # no 'private'
        caller_entity_id="ws-private",
    )
    ids = _ids(results)
    assert "private-001" not in ids, "Private engram must not appear without 'private' in tier_access"


def test_private_tier_visible_when_in_tier_access():
    """Caller with 'private' in tier_access and matching entity_id can see private engrams."""
    _store("private-visible-001", "ws-priv-ok", tier="private", entity_id="priv-entity")

    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-priv-ok",
        tier_access=["public", "project", "private"],
        caller_entity_id="priv-entity",
    )
    ids = _ids(results)
    assert "private-visible-001" in ids


def test_squad_tier_not_visible_without_squad_access():
    """Squad engrams must not appear if 'squad' is not in tier_access."""
    _store("squad-001", "ws-squad", tier="squad", entity_id="ws-squad")

    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-squad",
        tier_access=["public", "project"],  # no 'squad'
        caller_entity_id="ws-squad",
    )
    ids = _ids(results)
    assert "squad-001" not in ids


def test_admin_sees_all_tiers():
    """Admin (tier_access=None) must see engrams of all tiers."""
    _store("admin-pub", "ws-admin-tiers", tier="public")
    _store("admin-priv", "ws-admin-tiers", tier="private", entity_id="ws-admin-tiers")
    _store("admin-squad", "ws-admin-tiers", tier="squad", entity_id="ws-admin-tiers")
    _store("admin-project", "ws-admin-tiers", tier="project")
    _store("admin-entity", "ws-admin-tiers", tier="entity", entity_id="ws-admin-tiers")

    results = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-admin-tiers",
        tier_access=None,  # admin — no filter
    )
    ids = _ids(results)
    for cid in ["admin-pub", "admin-priv", "admin-squad", "admin-project", "admin-entity"]:
        assert cid in ids, f"Admin must see tier engram: {cid}"


# ---------------------------------------------------------------------------
# 4. update_engram_tier: coordinator promotes tier; recall returns updated tier
# ---------------------------------------------------------------------------

def test_update_engram_tier_changes_tier():
    """update_engram_tier() must change the tier and be reflected in subsequent searches."""
    _store("promote-001", "ws-promote", tier="project", entity_id="ws-promote")

    # Find the engram id
    results = _db.search_engrams(
        _VEC, threshold=0.0, limit=50, workspace_id="ws-promote"
    )
    target = next((r for r in results if r["context_id"] == "promote-001"), None)
    assert target is not None, "Test engram not found"
    engram_id = target["id"]

    # Promote to public
    updated = _db.update_engram_tier(engram_id, "public")
    assert updated is not None
    assert updated["tier"] == "public"
    assert updated["id"] == engram_id

    # Subsequent search must return the updated tier
    results_after = _db.search_engrams(
        _VEC, threshold=0.0, limit=50, workspace_id="ws-promote"
    )
    target_after = next((r for r in results_after if r["context_id"] == "promote-001"), None)
    assert target_after is not None
    assert target_after["tier"] == "public", f"Expected 'public', got {target_after['tier']!r}"


def test_update_engram_tier_invalid_tier_raises():
    """update_engram_tier with invalid tier must raise ValueError."""
    _store("invalid-tier-001", "ws-invalid-tier")
    results = _db.search_engrams(_VEC, threshold=0.0, limit=10, workspace_id="ws-invalid-tier")
    target = next((r for r in results if r["context_id"] == "invalid-tier-001"), None)
    assert target is not None

    with pytest.raises(ValueError, match="Invalid tier"):
        _db.update_engram_tier(target["id"], "superadmin")


def test_update_engram_tier_nonexistent_returns_none():
    """update_engram_tier on a nonexistent id must return None."""
    result = _db.update_engram_tier("00000000000000000000000000000000", "public")
    assert result is None


def test_promoted_engram_visible_after_tier_change():
    """After promoting from project to public, engram should appear for public-only callers."""
    _store("pub-promote-002", "ws-pub-promo", tier="project", entity_id="ws-pub-promo")

    # Before promotion — project tier not visible when caller has only 'public' access
    results_before = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-pub-promo",
        tier_access=["public"],
        caller_entity_id="ws-pub-promo",
    )
    assert "pub-promote-002" not in _ids(results_before), "Should not be visible pre-promotion"

    # Promote to public
    all_results = _db.search_engrams(_VEC, threshold=0.0, limit=50, workspace_id="ws-pub-promo")
    target = next(r for r in all_results if r["context_id"] == "pub-promote-002")
    _db.update_engram_tier(target["id"], "public")

    # After promotion — visible to public-only caller
    results_after = _db.search_engrams(
        _VEC,
        threshold=0.0,
        limit=50,
        workspace_id="ws-pub-promo",
        tier_access=["public"],
        caller_entity_id="ws-pub-promo",
    )
    assert "pub-promote-002" in _ids(results_after), "Should be visible after promotion to public"


# ---------------------------------------------------------------------------
# 5. Invalid tier rejected at upsert
# ---------------------------------------------------------------------------

def test_store_with_invalid_tier_raises():
    """upsert_engram with an invalid tier must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid tier"):
        _db.upsert_engram({
            "context_id": "bad-tier-ctx",
            "series": "test",
            "workspace_id": "ws-bad",
            "owner_type": "agent",
            "owner_id": "ws-bad",
            "raw_data": {"text": "bad tier"},
            "embedding": _VEC,
            "tier": "galaxy-brain",  # Invalid
        })


# ---------------------------------------------------------------------------
# 6. Backward-compat: existing tests not broken — workspace isolation still works
# ---------------------------------------------------------------------------

def test_workspace_isolation_preserved_with_tier():
    """Workspace isolation must still work correctly alongside tier filtering."""
    _store("ws-iso-a", "workspace-iso-a", tier="project")
    _store("ws-iso-b", "workspace-iso-b", tier="project")

    results_a = _db.search_engrams(
        _VEC, threshold=0.0, limit=50, workspace_id="workspace-iso-a",
        tier_access=["public", "project"]
    )
    ids_a = _ids(results_a)
    assert "ws-iso-a" in ids_a
    assert "ws-iso-b" not in ids_a
