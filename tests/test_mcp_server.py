"""Tests for the MCP SSE plugin — JSON-RPC 2.0 over SSE."""
import json
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_mcp.db"
pathlib.Path("/tmp/mirror_test_mcp.db").unlink(missing_ok=True)

ADMIN_TOKEN = "sk-mumega-internal-001"
os.environ["MIRROR_ADMIN_TOKEN"] = ADMIN_TOKEN

from fastapi import FastAPI
from plugins import loader as plugin_loader
from plugins.mcp_server.manifest import manifest as mcp_manifest

app = FastAPI()
plugin_loader.register(mcp_manifest)
plugin_loader.mount_all(app)

client = TestClient(app, raise_server_exceptions=False)


def _admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


# ---------------------------------------------------------------------------
# Tool listing — MCP initialize + tools/list
# ---------------------------------------------------------------------------

def test_mcp_tools_list_returns_memory_tools():
    """POST /mcp/rpc should handle tools/list and return mirror tools."""
    r = client.post(
        f"/mcp/{ADMIN_TOKEN}/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 1
    tool_names = [t["name"] for t in body["result"]["tools"]]
    assert "memory_search" in tool_names
    assert "memory_store" in tool_names
    assert "memory_recent" in tool_names


def test_mcp_invalid_token_returns_401():
    r = client.post(
        "/mcp/sk-totally-fake/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert r.status_code == 401


def test_mcp_unknown_method_returns_error():
    r = client.post(
        f"/mcp/{ADMIN_TOKEN}/rpc",
        json={"jsonrpc": "2.0", "id": 2, "method": "unknown/method", "params": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == -32601  # Method not found


def test_mcp_memory_search_tool_call():
    """tools/call for memory_search should return a result."""
    r = client.post(
        f"/mcp/{ADMIN_TOKEN}/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "memory_search",
                "arguments": {"query": "hello world", "top_k": 3, "threshold": 0.0},
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    # Result must have content array (MCP spec)
    assert "content" in body["result"]
