"""Tests for lease and signal coordination primitives."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_coordination.db"
pathlib.Path("/tmp/mirror_test_coordination.db").unlink(missing_ok=True)

from kernel.auth import TokenContext
from kernel.coordination import acquire_lease, receive_signals, release_lease, send_signal
from kernel.db import get_db
from plugins.mcp_server.tools import call_tool


def _ctx() -> TokenContext:
    return TokenContext(workspace_id="coord-ws", owner_type="agent", owner_id="agent-a")


def test_lease_acquire_conflict_and_release():
    db = get_db()
    first = acquire_lease(db, "deploy", "agent-a", 60, workspace_id="coord-ws")
    assert first["acquired"] is True

    second = acquire_lease(db, "deploy", "agent-b", 60, workspace_id="coord-ws")
    assert second["acquired"] is False
    assert second["held_by"] == "agent-a"

    assert release_lease(db, "deploy", "agent-b", workspace_id="coord-ws") == {"error": "not owner"}
    assert release_lease(db, "deploy", "agent-a", workspace_id="coord-ws") == {"released": True}


def test_expired_lease_can_be_reacquired():
    db = get_db()
    first = acquire_lease(db, "short", "agent-a", 1, workspace_id="coord-ws")
    assert first["acquired"] is True
    time.sleep(1.1)
    second = acquire_lease(db, "short", "agent-b", 60, workspace_id="coord-ws")
    assert second["acquired"] is True


def test_signal_send_receive_round_trip():
    db = get_db()
    sent = send_signal(
        db,
        to_agent="agent-b",
        from_agent="agent-a",
        signal_name="ready",
        payload={"ok": True},
        workspace_id="coord-ws",
    )
    assert sent["sent"] is True

    signals = receive_signals(db, "agent-b", workspace_id="coord-ws")
    assert signals[-1]["signal_id"] == sent["signal_id"]
    assert signals[-1]["from_agent"] == "agent-a"
    assert signals[-1]["signal_name"] == "ready"
    assert signals[-1]["payload"] == {"ok": True}


def test_mcp_coordination_tools_round_trip():
    ctx = _ctx()
    acquired = json.loads(call_tool(
        "memory_lease_acquire",
        {"lease_id": "mcp-lease", "agent": "agent-a", "ttl_seconds": 60},
        ctx,
    )["content"][0]["text"])
    assert acquired["acquired"] is True

    sent = json.loads(call_tool(
        "memory_signal_send",
        {"to_agent": "agent-b", "signal_name": "ping", "payload": {"n": 1}},
        ctx,
    )["content"][0]["text"])
    assert sent["sent"] is True

    received = json.loads(call_tool(
        "memory_signal_receive",
        {"agent": "agent-b"},
        ctx,
    )["content"][0]["text"])
    assert any(sig["signal_id"] == sent["signal_id"] for sig in received)
