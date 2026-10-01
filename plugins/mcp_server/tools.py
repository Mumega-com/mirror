"""MCP tool definitions and dispatch for Mirror memory tools."""
from __future__ import annotations

from typing import Any

from kernel.auth import TokenContext
from kernel.coordination import acquire_lease, receive_signals, release_lease, send_signal
from kernel.db import get_db
from kernel.embeddings import get_embedding
from kernel.outbox import is_outbox_enabled, make_outbox
from kernel.receipts import build_mirror_engram_write_receipt, emit_mirror_engram_write_receipt
from kernel.scoped_memory import (
    ScopeDenied,
    accept_projection,
    lookup_engram,
    scoped_count,
    scoped_recent,
    scoped_search,
    token_has_project_scope,
)
from kernel.search import hybrid_search

# ---------------------------------------------------------------------------
# Tool schemas (MCP spec format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "memory_search",
        "description": "Semantic search across Mirror memory engrams.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":     {"type": "string", "description": "Search query"},
                "top_k":     {"type": "integer", "default": 5, "description": "Max results"},
                "threshold": {"type": "number",  "default": 0.6, "description": "Min similarity"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_store",
        "description": "Store a memory engram in Mirror.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context_id":      {"type": "string"},
                "text":            {"type": "string"},
                "agent":           {"type": "string", "default": "mcp-client"},
                "epistemic_truths": {"type": "array",  "items": {"type": "string"}, "default": []},
                "core_concepts":   {"type": "array",  "items": {"type": "string"}, "default": []},
                "affective_vibe":  {"type": "string", "default": "Neutral"},
            },
            "required": ["context_id", "text"],
        },
    },
    {
        "name": "memory_lookup",
        "description": "Direct lookup of one engram in the caller's project. Hidden projects are not found.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context_id": {"type": "string"},
            },
            "required": ["context_id"],
        },
    },
    {
        "name": "memory_recent",
        "description": "List recent engrams from Mirror.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "default": "mcp-client"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "memory_lease_acquire",
        "description": "Acquire an exclusive coordination lease.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lease_id": {"type": "string"},
                "agent": {"type": "string"},
                "ttl_seconds": {"type": "integer", "default": 60},
            },
            "required": ["lease_id", "agent"],
        },
    },
    {
        "name": "memory_lease_release",
        "description": "Release a coordination lease held by this agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lease_id": {"type": "string"},
                "agent": {"type": "string"},
            },
            "required": ["lease_id", "agent"],
        },
    },
    {
        "name": "memory_signal_send",
        "description": "Send a coordination signal to another agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to_agent": {"type": "string"},
                "signal_name": {"type": "string"},
                "payload": {"type": "object", "default": {}},
            },
            "required": ["to_agent", "signal_name"],
        },
    },
    {
        "name": "memory_signal_receive",
        "description": "Receive coordination signals addressed to an agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "since_id": {"type": "string"},
            },
            "required": ["agent"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _content(data: Any) -> dict:
    """Wrap result in MCP content array."""
    import json
    return {"content": [{"type": "text", "text": json.dumps(data, default=str)}]}


def call_tool(name: str, arguments: dict, ctx: TokenContext) -> dict:
    db = get_db()
    workspace_id = None if ctx.is_admin else ctx.workspace_id

    if name == "memory_search":
        if token_has_project_scope(ctx):
            try:
                rows = scoped_search(db, ctx, arguments["query"], limit=int(arguments.get("top_k", 5)))
            except ScopeDenied as denied:
                return _content({"error": denied.code, "results": None, "count": 0})
            return _content([
                {
                    "context_id": r.get("context_id"),
                    "text": r.get("text") or "",
                    "title": r.get("title") or "",
                    "synthesized": bool(r.get("synthesized", False)),
                    "approved": False,
                }
                for r in rows
            ])
        query = arguments["query"]
        top_k = int(arguments.get("top_k", 5))
        threshold = float(arguments.get("threshold", 0.6))
        rows = hybrid_search(query, top_k, threshold, workspace_id, db)
        results = [
            {
                "context_id": r.get("context_id"),
                "series":     r.get("series"),
                "similarity": round(r.get("similarity", 0), 4),
                "text":       (r.get("raw_data") or {}).get("text", ""),
                "timestamp":  r.get("timestamp"),
            }
            for r in rows
        ]
        return _content(results)

    elif name == "memory_lookup":
        if not token_has_project_scope(ctx):
            return _content({"error": "not_found"})
        try:
            row = lookup_engram(db, ctx, str(arguments.get("context_id") or ""))
        except ScopeDenied as denied:
            return _content({"error": denied.code})
        if row is None:
            return _content({"error": "not_found"})
        return _content({
            "context_id": row.get("context_id"),
            "text": row.get("text") or "",
            "title": row.get("title") or "",
            "synthesized": bool(row.get("synthesized", False)),
            "approved": False,
        })

    elif name == "memory_store":
        if token_has_project_scope(ctx):
            try:
                stored = accept_projection(db, ctx, {
                    "slug": arguments.get("context_id"),
                    "text": arguments.get("text"),
                    "revision": arguments.get("revision") or 1,
                    "visibility": arguments.get("visibility") or "public",
                    "title": arguments.get("title") or "",
                    "project": arguments.get("project"),
                    "agent": arguments.get("agent"),
                    "tenant": arguments.get("tenant"),
                    "approved": arguments.get("approved"),
                    "idempotency_key": arguments.get("idempotency_key"),
                    "content_hash": arguments.get("content_hash"),
                })
            except ScopeDenied as denied:
                return _content({"error": denied.code, "stored": False, "approved": False})
            return _content(stored)
        from datetime import datetime, timezone
        import uuid
        context_id = arguments.get("context_id") or str(uuid.uuid4())
        text = arguments["text"]
        agent = arguments.get("agent", ctx.owner_id or "mcp-client")
        embedding = get_embedding(text)
        data = {
            "context_id": context_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "series": f"{agent.title()} - MCP Client",
            "workspace_id": workspace_id,
            "owner_type": ctx.owner_type,
            "owner_id": ctx.owner_id,
            "epistemic_truths": arguments.get("epistemic_truths", []),
            "core_concepts": arguments.get("core_concepts", []),
            "affective_vibe": arguments.get("affective_vibe", "Neutral"),
            "energy_level": "Balanced",
            "next_attractor": "",
            "raw_data": {
                "agent": agent,
                "text": text,
                "metadata": arguments.get("metadata", {}),
            },
            "embedding": embedding,
        }
        # F-16: route through outbox when enabled, else legacy fire-and-forget.
        outbox_id = None
        receipt = None
        if is_outbox_enabled() and hasattr(db, "upsert_engram_with_outbox"):
            payload = build_mirror_engram_write_receipt(data, actor=agent)
            # require_durable=True: refuse process-local MemoryOutbox for
            # the atomic-txn helper (BLOCK-P1-7).
            outbox = make_outbox(db, require_durable=True)
            outbox_id = db.upsert_engram_with_outbox(data, payload, outbox)
        else:
            db.upsert_engram(data)
            receipt = emit_mirror_engram_write_receipt(data, actor=agent)
        return _content({
            "stored": True,
            "context_id": context_id,
            "receipt": receipt.get("receipt") if isinstance(receipt, dict) else None,
            "outbox_id": outbox_id,
        })

    elif name == "memory_recent":
        if token_has_project_scope(ctx):
            try:
                rows = scoped_recent(db, ctx, limit=int(arguments.get("limit", 10)))
                count = scoped_count(db, ctx)
            except ScopeDenied as denied:
                return _content({"error": denied.code, "engrams": [], "count": 0})
            return _content({
                "count": count,
                "engrams": [
                    {
                        "context_id": r.get("context_id"),
                        "text": r.get("text") or "",
                        "title": r.get("title") or "",
                        "synthesized": bool(r.get("synthesized", False)),
                        "approved": False,
                    }
                    for r in rows
                ],
            })
        agent = arguments.get("agent", ctx.owner_id or "mcp-client")
        limit = int(arguments.get("limit", 10))
        rows = db.recent_engrams(agent, limit=limit, workspace_id=workspace_id)
        return _content([
            {
                "context_id": r.get("context_id"),
                "series":     r.get("series"),
                "timestamp":  r.get("timestamp"),
                "text":       (r.get("raw_data") or {}).get("text", ""),
            }
            for r in rows
        ])

    elif name == "memory_lease_acquire":
        return _content(acquire_lease(
            db,
            arguments["lease_id"],
            arguments["agent"],
            int(arguments.get("ttl_seconds", 60)),
            workspace_id=workspace_id,
        ))

    elif name == "memory_lease_release":
        return _content(release_lease(
            db,
            arguments["lease_id"],
            arguments["agent"],
            workspace_id=workspace_id,
        ))

    elif name == "memory_signal_send":
        from_agent = ctx.owner_id or arguments.get("from_agent") or "mcp-client"
        return _content(send_signal(
            db,
            arguments["to_agent"],
            from_agent,
            arguments["signal_name"],
            arguments.get("payload") or {},
            workspace_id=workspace_id,
        ))

    elif name == "memory_signal_receive":
        return _content(receive_signals(
            db,
            arguments["agent"],
            since_id=arguments.get("since_id"),
            workspace_id=workspace_id,
        ))

    else:
        raise ValueError(f"Unknown tool: {name}")
