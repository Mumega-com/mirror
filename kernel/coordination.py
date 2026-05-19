"""Multi-agent coordination primitives."""
from __future__ import annotations

from typing import Any, Optional


def acquire_lease(
    db: Any,
    lease_id: str,
    agent: str,
    ttl_seconds: int = 60,
    workspace_id: Optional[str] = None,
) -> dict:
    return db.acquire_lease(lease_id, agent, ttl_seconds, workspace_id=workspace_id)


def release_lease(
    db: Any,
    lease_id: str,
    agent: str,
    workspace_id: Optional[str] = None,
) -> dict:
    return db.release_lease(lease_id, agent, workspace_id=workspace_id)


def send_signal(
    db: Any,
    to_agent: str,
    from_agent: str,
    signal_name: str,
    payload: Optional[dict] = None,
    workspace_id: Optional[str] = None,
) -> dict:
    return db.send_signal(to_agent, from_agent, signal_name, payload or {}, workspace_id=workspace_id)


def receive_signals(
    db: Any,
    agent: str,
    since_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> list[dict]:
    return db.receive_signals(agent, since_id=since_id, workspace_id=workspace_id)
