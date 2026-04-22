"""Tests for workspace isolation — engrams are hard-scoped by workspace_id."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_ws.db"

# Fresh DB for each test module run
import pathlib
pathlib.Path("/tmp/mirror_test_ws.db").unlink(missing_ok=True)

from kernel.db import get_db

_db = get_db()

_VEC = [0.1] * 1536
_VEC_B = [0.2] * 1536  # slightly different so search returns distinct results


def _store(context_id: str, workspace_id: str, text: str = "hello") -> None:
    _db.upsert_engram({
        "context_id": context_id,
        "series": "test",
        "workspace_id": workspace_id,
        "owner_type": "agent",
        "owner_id": workspace_id,
        "raw_data": {"text": text},
        "embedding": _VEC,
    })


# ---------------------------------------------------------------------------
# Workspace hard isolation
# ---------------------------------------------------------------------------

def test_search_returns_only_own_workspace():
    _store("ws-a-1", "workspace-a")
    _store("ws-b-1", "workspace-b")

    results = _db.search_engrams(_VEC, threshold=0.5, limit=10, workspace_id="workspace-a")
    ids = [r["context_id"] for r in results]

    assert "ws-a-1" in ids
    assert "ws-b-1" not in ids


def test_search_without_workspace_filter_sees_all():
    """Admin path — no workspace_id means unrestricted."""
    _store("ws-a-2", "workspace-a")
    _store("ws-b-2", "workspace-b")

    results = _db.search_engrams(_VEC, threshold=0.5, limit=10)
    ids = [r["context_id"] for r in results]

    assert "ws-a-2" in ids
    assert "ws-b-2" in ids


def test_recent_returns_only_own_workspace():
    _store("ws-a-rec", "workspace-a-rec")
    _store("ws-b-rec", "workspace-b-rec")

    results = _db.recent_engrams("test", limit=20, workspace_id="workspace-a-rec")
    ids = [r["context_id"] for r in results]

    assert "ws-a-rec" in ids
    assert "ws-b-rec" not in ids


def test_upsert_preserves_workspace_id():
    _store("ws-check-1", "workspace-check")
    results = _db.search_engrams(_VEC, threshold=0.5, limit=10, workspace_id="workspace-check")
    assert results
    assert results[0]["workspace_id"] == "workspace-check"


def test_cross_workspace_leakage_impossible():
    """Storing 100 engrams in workspace-x — workspace-y search returns none of them."""
    for i in range(5):
        _store(f"ws-x-{i}", "workspace-x")

    results = _db.search_engrams(_VEC, threshold=0.5, limit=20, workspace_id="workspace-y-isolated")
    ids = [r["context_id"] for r in results]
    for i in range(5):
        assert f"ws-x-{i}" not in ids
