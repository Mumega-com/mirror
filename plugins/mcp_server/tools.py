"""MCP tool definitions and dispatch for Mirror memory tools."""
from __future__ import annotations

from typing import Any

from kernel.auth import TokenContext
from kernel.db import get_db
from kernel.embeddings import get_embedding

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
        query = arguments["query"]
        top_k = int(arguments.get("top_k", 5))
        threshold = float(arguments.get("threshold", 0.6))
        embedding = get_embedding(query)
        rows = db.search_engrams(
            embedding=embedding,
            threshold=threshold,
            limit=top_k,
            workspace_id=workspace_id,
        )
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

    elif name == "memory_store":
        from datetime import datetime, timezone
        import uuid
        context_id = arguments.get("context_id") or str(uuid.uuid4())
        text = arguments["text"]
        agent = arguments.get("agent", ctx.owner_id or "mcp-client")
        embedding = get_embedding(text)
        db.upsert_engram({
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
            "raw_data": {"agent": agent, "text": text},
            "embedding": embedding,
        })
        return _content({"stored": True, "context_id": context_id})

    elif name == "memory_recent":
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

    else:
        raise ValueError(f"Unknown tool: {name}")
