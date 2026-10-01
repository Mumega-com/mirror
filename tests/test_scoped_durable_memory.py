"""Durable, access-scoped projection of two synthetic projects.

The original SQLite store keeps one row per context_id, so a second project
reusing that id replaces the first. These tests use the scoped path, which
keeps the native governed-write path off.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_EMBED_PROVIDER"] = "local"

from kernel.auth import TokenContext
from kernel.db_sqlite import SQLiteDB
from kernel.scoped_memory import (
    ScopeDenied,
    accept_projection,
    grant_project,
    lookup_engram,
    read_audit,
    receipt_count,
    revoke_project,
    scoped_count,
    scoped_recent,
    scoped_search,
    withdraw_engram,
)
from plugins.mcp_server.tools import call_tool


ALPHA_PUBLIC = "What Mirror is: a working memory of the actual work. ALPHA-PUBLIC"
BETA_PUBLIC = "What Mirror is inside Beta. BETA-SECRET-TITLE BETA-SECRET-BODY"
ALPHA_PRIVATE = "How this business works at Alpha. ALPHA-PRIVATE"
BETA_PRIVATE = "How this business works at Beta. BETA-PRIVATE"


def _ctx(project_id: str, principal_id: str) -> TokenContext:
    return TokenContext(
        workspace_id="tenant-synth",
        owner_type="principal",
        owner_id=principal_id,
        project_id=project_id,
        principal_id=principal_id,
        tier_access=["public", "project", "private"],
    )


@pytest.fixture()
def db():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    database = SQLiteDB(handle.name, dims=32)
    grant_project(database, tenant_id="tenant-synth", project_id="alpha", principal_id="ada", can_read_private=True)
    grant_project(database, tenant_id="tenant-synth", project_id="beta", principal_id="bea", can_read_private=True)
    grant_project(database, tenant_id="tenant-synth", project_id="alpha", principal_id="vic", can_read_private=False)
    yield database
    os.unlink(handle.name)


def _project(database, ctx, slug, text, revision=1, **extra):
    return accept_projection(database, ctx, {
        "slug": slug,
        "text": text,
        "revision": revision,
        "title": extra.pop("title", text.split(".")[0]),
        "project": extra.pop("project", "other-project"),
        "agent": extra.pop("agent", "caller-chosen-agent"),
        "tenant": extra.pop("tenant", "caller-tenant"),
        "approved": extra.pop("approved", True),
        "synthesized": True,
        **extra,
    })


def test_identical_slugs_stay_distinct_and_survive_reopen(db):
    ada = _ctx("alpha", "ada")
    bea = _ctx("beta", "bea")
    _project(db, ada, "what-mirror-is", ALPHA_PUBLIC, visibility="public")
    _project(db, bea, "what-mirror-is", BETA_PUBLIC, visibility="public")
    _project(db, ada, "how-this-business-works", ALPHA_PRIVATE, visibility="private")
    _project(db, bea, "how-this-business-works", BETA_PRIVATE, visibility="private")

    reopened = SQLiteDB(db.db_path, dims=32)
    alpha = lookup_engram(reopened, ada, "what-mirror-is")
    beta = lookup_engram(reopened, bea, "what-mirror-is")
    assert alpha["text"] == ALPHA_PUBLIC
    assert beta["text"] == BETA_PUBLIC
    assert alpha["approved"] is False
    assert alpha["synthesized"] is False
    assert alpha["source_engram_ids"] == []
    assert beta["text"] != alpha["text"]


def test_caller_metadata_does_not_grant_the_other_project(db):
    ada = _ctx("alpha", "ada")
    stored = _project(db, ada, "what-mirror-is", ALPHA_PUBLIC, project="beta", agent="bea", tenant="beta-tenant")
    assert stored["status"] == "accepted"
    row = lookup_engram(db, ada, "what-mirror-is")
    assert row["project"] == "alpha"
    assert row["workspace_id"] == "tenant-synth"
    assert lookup_engram(db, _ctx("beta", "bea"), "what-mirror-is") is None


def test_newer_revision_wins_and_retry_is_idempotent(db):
    ada = _ctx("alpha", "ada")
    _project(db, ada, "what-mirror-is", "revision one", revision=1)
    updated = _project(db, ada, "what-mirror-is", "revision two ALPHA-CURRENT", revision=2)
    assert updated["status"] == "accepted"
    stale = _project(db, ada, "what-mirror-is", "revision one replay", revision=1)
    assert stale["status"] == "rejected_stale"
    assert lookup_engram(db, ada, "what-mirror-is")["text"] == "revision two ALPHA-CURRENT"

    again = _project(db, ada, "what-mirror-is", "revision two ALPHA-CURRENT", revision=2)
    assert again["duplicate"] is True
    assert again["receipt_id"] == updated["receipt_id"]
    assert receipt_count(db, ada, "what-mirror-is") == 2  # rev 1 and rev 2, not a third


def test_withdraw_hides_the_record_and_keeps_the_audit(db):
    ada = _ctx("alpha", "ada")
    _project(db, ada, "what-mirror-is", ALPHA_PUBLIC)
    assert withdraw_engram(db, ada, "what-mirror-is")["status"] == "withdrawn"
    assert lookup_engram(db, ada, "what-mirror-is") is None
    assert scoped_search(db, ada, "ALPHA-PUBLIC") == []
    audit = read_audit(db, ada, "what-mirror-is")
    assert audit is not None
    assert audit["archived"] is True
    assert "ALPHA-PUBLIC" in audit["text"]
    assert any(item["action"] == "withdrawn" for item in audit["audit"])
    assert audit["receipts"]
    with db._conn() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM mirror_engrams").fetchone()[0]
    assert remaining == 1


def test_revoked_principal_is_excluded_without_erasing_the_row(db):
    ada = _ctx("alpha", "ada")
    _project(db, ada, "what-mirror-is", ALPHA_PUBLIC)
    revoke_project(db, tenant_id="tenant-synth", project_id="alpha", principal_id="ada")
    with pytest.raises(ScopeDenied) as denied:
        lookup_engram(db, ada, "what-mirror-is")
    assert denied.value.code == "grant_revoked"
    with pytest.raises(ScopeDenied):
        scoped_search(db, ada, "ALPHA-PUBLIC")
    with db._conn() as conn:
        raw = conn.execute("SELECT raw_data FROM mirror_engrams").fetchone()[0]
    assert "ALPHA-PUBLIC" in raw


def test_other_project_is_absent_from_search_lookup_listings_and_mcp(db, monkeypatch):
    ada = _ctx("alpha", "ada")
    bea = _ctx("beta", "bea")
    vic = _ctx("alpha", "vic")
    _project(db, ada, "what-mirror-is", ALPHA_PUBLIC, visibility="public", title="What Mirror is")
    _project(db, bea, "what-mirror-is", BETA_PUBLIC, visibility="public", title="BETA-SECRET-TITLE")
    _project(db, ada, "how-this-business-works", ALPHA_PRIVATE, visibility="private", title="How Alpha works")
    _project(db, bea, "how-this-business-works", BETA_PRIVATE, visibility="private", title="BETA-PRIVATE")

    assert "BETA-SECRET" not in json.dumps(scoped_search(db, ada, "BETA-SECRET"))
    assert lookup_engram(db, ada, "what-mirror-is")["text"] == ALPHA_PUBLIC
    assert "BETA-SECRET" not in json.dumps(scoped_recent(db, ada, limit=20))
    assert scoped_count(db, ada) == 2
    assert scoped_count(db, vic) == 1
    assert lookup_engram(db, vic, "how-this-business-works") is None
    assert "ALPHA-PRIVATE" not in json.dumps(scoped_search(db, vic, "ALPHA-PRIVATE"))

    import kernel.db as dbmod
    monkeypatch.setenv("MIRROR_BACKEND", "sqlite")
    monkeypatch.setenv("MIRROR_SQLITE_PATH", db.db_path)
    monkeypatch.setenv("MIRROR_VECTOR_DIMS", "32")
    monkeypatch.setenv("MIRROR_EMBED_PROVIDER", "local")
    dbmod._db_singleton = None
    dbmod._db_singleton_signature = None

    search = json.loads(call_tool("memory_search", {"query": "BETA-SECRET", "agent": "bea", "project": "beta"}, ada)["content"][0]["text"])
    lookup = json.loads(call_tool("memory_lookup", {"context_id": "what-mirror-is", "project": "beta", "agent": "bea"}, ada)["content"][0]["text"])
    recent = json.loads(call_tool("memory_recent", {"agent": "bea", "limit": 20}, ada)["content"][0]["text"])
    blob = json.dumps({"search": search, "lookup": lookup, "recent": recent})
    assert "BETA-SECRET" not in blob
    assert "BETA-PRIVATE" not in blob
    assert lookup["text"] == ALPHA_PUBLIC
    assert recent["count"] == 2

    dbmod._db_singleton = None
    dbmod._db_singleton_signature = None


def test_failed_projection_is_visible_and_native_write_stays_off(db):
    ada = _ctx("alpha", "ada")
    failed = accept_projection(db, ada, {"slug": "", "text": "no slug", "approved": True})
    assert failed["status"] == "failed"
    assert failed != []
    assert scoped_count(db, ada) == 0

    import plugins.mcp_server.tools as tools
    import plugins.memory.routes as routes
    assert "kernel.projection_authority" not in sys.modules
    assert "kernel.decision_shadow" not in sys.modules
    for module in (tools, routes):
        source = open(module.__file__, encoding="utf-8").read()
        assert "projection_authority" not in source
        assert "decision_shadow" not in source
        assert "approval_expired" not in source
