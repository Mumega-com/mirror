"""Tests that routes pass workspace_id from TokenContext into DB queries."""
import json
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_routes_ws.db"
pathlib.Path("/tmp/mirror_test_routes_ws.db").unlink(missing_ok=True)

ADMIN_TOKEN = "sk-mumega-internal-001"
TENANT_KEY = "sk-tenant-ws-route-test"
TENANT_SLUG = "acme-corp"

# Write a tenant_keys.json for this test session
_tenant_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump([{"key": TENANT_KEY, "agent_slug": TENANT_SLUG, "active": True}], _tenant_file)
_tenant_file.flush()

# Patch env so resolve_token_context picks up our test file
os.environ["MIRROR_TENANT_KEYS_PATH"] = _tenant_file.name
os.environ["MIRROR_ADMIN_TOKEN"] = ADMIN_TOKEN

# Build minimal FastAPI app with memory plugin
from fastapi import FastAPI
from plugins import loader as plugin_loader
from plugins.memory.manifest import manifest as memory_manifest

app = FastAPI()
plugin_loader.register(memory_manifest)
plugin_loader.mount_all(app)

client = TestClient(app, raise_server_exceptions=False)


def _admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _tenant_headers():
    return {"Authorization": f"Bearer {TENANT_KEY}"}


def _store(headers, context_id, text="hello world"):
    return client.post("/store", json={
        "context_id": context_id,
        "agent": "test-agent",
        "text": text,
        "epistemic_truths": [],
        "core_concepts": [],
        "affective_vibe": "Neutral",
        "energy_level": "Balanced",
        "next_attractor": "",
        "metadata": {},
    }, headers=headers)


def _search(headers, query="knowledge"):
    return client.post("/search", json={
        "query": query, "top_k": 20, "threshold": 0.0,
    }, headers=headers)


# ---------------------------------------------------------------------------

def test_no_token_returns_401():
    r = client.post("/search", json={"query": "x", "top_k": 5, "threshold": 0.0})
    assert r.status_code == 401


def test_tenant_store_tags_workspace():
    r = _store(_tenant_headers(), "tenant-ws-1", "acme proprietary data")
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == TENANT_SLUG


def test_admin_store_has_no_workspace():
    r = _store(_admin_headers(), "admin-ws-1", "global admin data")
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] is None


def test_tenant_search_excludes_admin_engrams():
    # Use the same phrase in query as in stored text so ONNX similarity is high
    _store(_admin_headers(), "admin-isolation-1", "cascade memory system")
    _store(_tenant_headers(), "tenant-isolation-1", "cascade memory system")

    r = _search(_tenant_headers(), query="cascade memory system")
    assert r.status_code == 200
    ids = [e["context_id"] for e in r.json()]
    assert "tenant-isolation-1" in ids
    assert "admin-isolation-1" not in ids


def test_admin_search_sees_all_workspaces():
    _store(_admin_headers(), "admin-all-1", "cascade memory system global")
    _store(_tenant_headers(), "tenant-all-1", "cascade memory system tenant")

    r = _search(_admin_headers(), query="cascade memory system")
    assert r.status_code == 200
    ids = [e["context_id"] for e in r.json()]
    assert "admin-all-1" in ids
    assert "tenant-all-1" in ids
