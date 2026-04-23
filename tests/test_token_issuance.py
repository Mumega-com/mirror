"""Tests for token issuance DB methods and auth resolution."""
import hashlib
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

os.environ["MIRROR_BACKEND"] = "local"
# Uses DATABASE_URL from environment (set in .env)

import pytest
from kernel.db import get_db, LocalDB


@pytest.fixture
def db():
    # Force LocalDB regardless of module-level MIRROR_BACKEND cached state
    os.environ["MIRROR_BACKEND"] = "local"
    _db = LocalDB()
    return _db


@pytest.fixture
def workspace(db):
    """Create a test workspace, yield it, then clean up."""
    ws = db.create_workspace(slug="test-ws-001", name="Test Workspace")
    yield ws
    # Cleanup — delete tokens first (FK), then workspace
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mirror_tokens WHERE workspace_id = %s", [ws["id"]])
        cur.execute("DELETE FROM mirror_workspaces WHERE id = %s", [ws["id"]])


def test_create_workspace(db):
    ws = db.create_workspace(slug="test-ws-002", name="My Team")
    assert ws["id"].startswith("ws-")
    assert ws["slug"] == "test-ws-002"
    assert ws["name"] == "My Team"
    assert ws["active"] is True
    # Cleanup
    with db._conn() as conn:
        conn.cursor().execute("DELETE FROM mirror_workspaces WHERE id = %s", [ws["id"]])


def test_list_workspaces(db, workspace):
    workspaces = db.list_workspaces()
    ids = [w["id"] for w in workspaces]
    assert workspace["id"] in ids


def test_issue_token_returns_plaintext(db, workspace):
    result = db.issue_token(
        workspace_id=workspace["id"],
        label="test-agent",
        token_type="agent",
        owner_id="test-agent",
    )
    assert "token" in result        # plaintext returned once
    assert "token_id" in result
    assert result["token"].startswith("sk-")
    assert result["workspace_id"] == workspace["id"]


def test_issued_token_hash_stored(db, workspace):
    result = db.issue_token(
        workspace_id=workspace["id"],
        label="hash-check",
        token_type="agent",
        owner_id="test",
    )
    token_hash = hashlib.sha256(result["token"].encode()).hexdigest()
    resolved = db.resolve_token_from_db(token_hash)
    assert resolved is not None
    assert resolved["workspace_id"] == workspace["id"]


def test_list_tokens(db, workspace):
    db.issue_token(workspace_id=workspace["id"], label="tok-a", token_type="agent", owner_id="a")
    db.issue_token(workspace_id=workspace["id"], label="tok-b", token_type="readonly", owner_id=None)
    tokens = db.list_tokens(workspace["id"])
    assert len(tokens) >= 2
    labels = [t["label"] for t in tokens]
    assert "tok-a" in labels
    assert "tok-b" in labels


def test_revoke_token(db, workspace):
    result = db.issue_token(
        workspace_id=workspace["id"],
        label="revoke-me",
        token_type="agent",
        owner_id="test",
    )
    token_hash = hashlib.sha256(result["token"].encode()).hexdigest()
    db.revoke_token(result["token_id"])
    resolved = db.resolve_token_from_db(token_hash)
    assert resolved is None  # revoked tokens are not resolved


def test_resolve_token_from_db_unknown(db):
    assert db.resolve_token_from_db("notahash") is None


def test_resolve_token_from_db_sets_last_used(db, workspace):
    result = db.issue_token(
        workspace_id=workspace["id"],
        label="last-used",
        token_type="agent",
        owner_id="test",
    )
    token_hash = hashlib.sha256(result["token"].encode()).hexdigest()
    resolved = db.resolve_token_from_db(token_hash)
    assert resolved is not None
    # last_used_at should now be set
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT last_used_at FROM mirror_tokens WHERE id = %s", [result["token_id"]])
            row = cur.fetchone()
    assert row[0] is not None
