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
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_receipts.db"
pathlib.Path("/tmp/mirror_test_receipts.db").unlink(missing_ok=True)

ADMIN_TOKEN = "sk-mumega-internal-001"
TENANT_KEY = "sk-tenant-receipt-route-test"
TENANT_SLUG = "codex-smoke"

_tenant_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump([{"key": TENANT_KEY, "agent_slug": TENANT_SLUG, "active": True}], _tenant_file)
_tenant_file.flush()

os.environ["MIRROR_TENANT_KEYS_PATH"] = _tenant_file.name
os.environ["MIRROR_ADMIN_TOKEN"] = ADMIN_TOKEN

from plugins import loader as plugin_loader
from plugins.memory.manifest import manifest as memory_manifest

app = FastAPI()
plugin_loader.register(memory_manifest)
plugin_loader.mount_all(app)

client = TestClient(app, raise_server_exceptions=False)


def test_store_emits_mirror_receipt_after_write(monkeypatch) -> None:
    calls = []

    def fake_emit(data, *, merged=False, actor=None, client=None):
        calls.append({
            "context_id": data["context_id"],
            "actor": actor,
            "merged": merged,
            "sos_task_id": data["raw_data"]["metadata"]["sos_task_id"],
        })
        return {"ok": True, "receipt": {"id": "receipt-mirror-1"}}

    monkeypatch.setattr(
        "plugins.memory.routes.emit_mirror_engram_write_receipt",
        fake_emit,
    )

    response = client.post(
        "/store",
        json={
            "context_id": "task:abc:done",
            "agent": "codex",
            "text": "Mirror receipt smoke",
            "metadata": {"sos_task_id": "task-abc"},
        },
        headers={"Authorization": f"Bearer {TENANT_KEY}"},
    )

    assert response.status_code == 200
    assert response.json()["receipt"] == {"id": "receipt-mirror-1"}
    assert calls == [{
        "context_id": "task:abc:done",
        "actor": TENANT_SLUG,
        "merged": False,
        "sos_task_id": "task-abc",
    }]
