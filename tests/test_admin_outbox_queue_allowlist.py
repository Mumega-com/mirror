"""WARN-F16-005 — outbox admin endpoints reject unknown queue names.

The /admin/outbox/status and /admin/outbox/dlq endpoints accept a ?queue=
query parameter. Today the only legitimate queue is "inkwell-receipts";
any other value should be rejected at the route boundary with HTTP 422
("queue_not_allowed") so callers cannot poke arbitrary queue names against
the operator surface.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_admin_outbox_queue.db"
pathlib.Path("/tmp/mirror_test_admin_outbox_queue.db").unlink(missing_ok=True)

ADMIN_TOKEN = "sk-mumega-internal-001"
os.environ["MIRROR_ADMIN_TOKEN"] = ADMIN_TOKEN

# Tenant keys file is required even if we don't use a tenant token here.
_tenant_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump([], _tenant_file)
_tenant_file.flush()
os.environ["MIRROR_TENANT_KEYS_PATH"] = _tenant_file.name

from plugins import loader as plugin_loader
from plugins.admin.manifest import manifest as admin_manifest

app = FastAPI()
plugin_loader.register(admin_manifest)
plugin_loader.mount_all(app)

client = TestClient(app, raise_server_exceptions=False)


def _admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def test_status_allows_default_queue(monkeypatch):
    monkeypatch.delenv("MIRROR_OUTBOX_ENABLED", raising=False)
    r = client.get("/admin/outbox/status", headers=_admin_headers())
    # Without the flag the endpoint short-circuits to backend=disabled — which
    # still proves the queue passed validation.
    assert r.status_code == 200
    assert r.json()["backend"] == "disabled"


def test_status_rejects_unknown_queue():
    r = client.get(
        "/admin/outbox/status",
        params={"queue": "evil-queue"},
        headers=_admin_headers(),
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "queue_not_allowed"


def test_dlq_rejects_unknown_queue():
    r = client.get(
        "/admin/outbox/dlq",
        params={"queue": "../../etc/passwd"},
        headers=_admin_headers(),
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "queue_not_allowed"


def test_status_requires_admin_token():
    r = client.get("/admin/outbox/status")
    # 401 (no token) or 403 (non-admin) are both acceptable rejections; the
    # contract is "non-admins do not reach the queue handler".
    assert r.status_code in (401, 403)
