"""
Tests for Mirror v1.0 — scope field, session engrams, X-Project-Context header.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"

from kernel.db_sqlite import SQLiteDB
from kernel.types import SearchRequest

# A unit embedding — all 1536 dims set to the same value so cosine similarity
# between two identical vectors is 1.0 (normalised dot product = 1.0).
_VEC_HIGH = [1.0 / (1536 ** 0.5)] * 1536  # normalised: cosine(self, self) = 1.0
_VEC_LOW  = [0.0] * 1535 + [1.0]           # orthogonal — cosine with _VEC_HIGH ≈ 0


@pytest.fixture
def db(tmp_path):
    """Fresh isolated SQLite DB per test — no cross-test pollution."""
    return SQLiteDB(db_path=str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# Test 1: scope field on SearchRequest
# ---------------------------------------------------------------------------

def test_scope_field_on_search_request() -> None:
    """SearchRequest accepts and preserves scope='session'."""
    req = SearchRequest(query="x", scope="session")
    assert req.scope == "session"


def test_scope_field_defaults_to_none() -> None:
    """scope defaults to None when not provided."""
    req = SearchRequest(query="y")
    assert req.scope is None


# ---------------------------------------------------------------------------
# Test 2: session engram stored with low importance_score and memory_tier
# ---------------------------------------------------------------------------

def test_session_engram_stored_with_low_importance(db: SQLiteDB) -> None:
    """Storing an engram with owner_type='session' writes importance_score=0.05."""
    db.upsert_engram({
        "context_id": "sess-engram-001",
        "series": "test-session",
        "owner_type": "session",
        "owner_id": "sess-abc",
        "importance_score": 0.05,
        "memory_tier": "working",
        "raw_data": {"text": "session working memory"},
        "embedding": _VEC_HIGH,
    })

    # Retrieve via table API (bypasses importance filter)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT importance_score, memory_tier FROM mirror_engrams WHERE context_id = ?",
            ("sess-engram-001",),
        ).fetchone()

    assert row is not None, "engram not found"
    assert row["importance_score"] == pytest.approx(0.05)
    assert row["memory_tier"] == "working"


# ---------------------------------------------------------------------------
# Test 3: session engram is invisible to standard recall (threshold=0.5)
# ---------------------------------------------------------------------------

def test_session_engram_below_search_threshold(db: SQLiteDB) -> None:
    """A session engram (importance_score=0.05) must not appear in search results
    when threshold=0.5, even if its vector similarity is high.
    """
    # Store a high-similarity session engram
    db.upsert_engram({
        "context_id": "sess-invisible-001",
        "series": "test-invisible",
        "owner_type": "session",
        "owner_id": "sess-xyz",
        "importance_score": 0.05,
        "memory_tier": "working",
        "raw_data": {"text": "invisible session engram"},
        "embedding": _VEC_HIGH,
    })

    # Also store a normal engram so we know the search works at all
    db.upsert_engram({
        "context_id": "normal-visible-001",
        "series": "test-visible",
        "owner_type": "agent",
        "owner_id": "agent-visible",
        "importance_score": 1.0,
        "memory_tier": "episodic",
        "raw_data": {"text": "normal visible engram"},
        "embedding": _VEC_HIGH,
    })

    results = db.search_engrams(
        embedding=_VEC_HIGH,
        threshold=0.5,
        limit=10,
    )
    context_ids = [r["context_id"] for r in results]

    # Session engram must be excluded (importance_score 0.05 < threshold 0.5)
    assert "sess-invisible-001" not in context_ids, (
        "session engram should be invisible to standard recall"
    )
    # Normal engram must be visible
    assert "normal-visible-001" in context_ids, (
        "normal engram should appear in search results"
    )


# ---------------------------------------------------------------------------
# Test 4: X-Project-Context overrides project filter in HTTP route
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugins import loader as plugin_loader
from plugins.memory.manifest import manifest as memory_manifest

ADMIN_TOKEN = "sk-mumega-internal-001"

_tenant_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump([], _tenant_file)
_tenant_file.flush()

os.environ["MIRROR_TENANT_KEYS_PATH"] = _tenant_file.name
os.environ["MIRROR_ADMIN_TOKEN"] = ADMIN_TOKEN

_app = FastAPI()
plugin_loader.register(memory_manifest)
plugin_loader.mount_all(_app)
_client = TestClient(_app, raise_server_exceptions=False)


def _admin_headers(extra: dict | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    if extra:
        h.update(extra)
    return h


def test_x_project_context_overrides_project_filter(
    db: SQLiteDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engrams stored with owner_type='project'/owner_id='proj-v1'
    must appear when X-Project-Context: proj-v1 is sent by an admin caller.

    We bypass the live embedding service entirely:
    - Store the project engram directly via db.upsert_engram with a known vector.
    - Patch plugins.memory.routes._get_db so the /search route uses the same
      isolated DB instance — guaranteeing it sees only this test's engrams.
    - Patch plugins.memory.routes._get_embedding_http so the /search query also
      returns that same vector — guaranteeing cosine similarity = 1.0.
    This mirrors the pattern used in test_session_engram_below_search_threshold.
    """
    # Store the project engram directly — no live encoder needed.
    db.upsert_engram({
        "context_id": "proj-engram-v1-001",
        "series": "test-project",
        "owner_type": "project",
        "owner_id": "proj-v1",
        "importance_score": 1.0,
        "memory_tier": "episodic",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "raw_data": {"text": "unique project context engram xyzzy"},
        "embedding": _VEC_HIGH,
    })

    # Patch _get_db in the routes module so the HTTP handler uses our isolated
    # per-test DB, not whatever the global env var points to.
    import plugins.memory.routes as _routes
    monkeypatch.setattr(_routes, "_get_db", lambda: db)

    # Patch the embedding function used inside the /search route so the query
    # vector matches the stored engram without hitting a real encoder.
    monkeypatch.setattr(_routes, "_get_embedding_http", lambda text: _VEC_HIGH)

    r = _client.post(
        "/search",
        json={
            "query": "unique project context engram xyzzy",
            "top_k": 10,
            "threshold": 0.0,
        },
        headers=_admin_headers({"X-Project-Context": "proj-v1"}),
    )
    assert r.status_code == 200, f"unexpected status: {r.status_code} — {r.text}"

    context_ids = [e["context_id"] for e in r.json()]
    assert "proj-engram-v1-001" in context_ids, (
        f"project engram not found in results: {context_ids}"
    )
