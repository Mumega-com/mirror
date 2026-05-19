#!/usr/bin/env python3
"""Mirror MCP stdio server.

Wraps the Mirror HTTP API as an MCP tool server. Authentication uses the
standalone MIRROR_API_KEY environment variable.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_API_KEY = os.environ.get("MIRROR_API_KEY", "")
DEFAULT_AGENT = os.environ.get("MIRROR_AGENT", "mcp-client")


def make_response(id_, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": id_}
    if error:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp


def mirror_request(method: str, path: str, body: dict | None = None) -> dict:
    if not MIRROR_API_KEY:
        return {"error": "MIRROR_API_KEY is required"}
    url = f"{MIRROR_URL}{path}"
    headers = {
        "Authorization": f"Bearer {MIRROR_API_KEY}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except URLError as exc:
        return {"error": str(exc)}


def get_tools() -> list[dict]:
    return [
        {
            "name": "remember",
            "description": "Store a memory in Mirror.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "agent_id": {"type": "string", "default": DEFAULT_AGENT},
                    "metadata": {"type": "object", "default": {}},
                },
                "required": ["content"],
            },
        },
        {
            "name": "recall",
            "description": "Search Mirror memory by meaning.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "agent_id": {"type": "string", "default": DEFAULT_AGENT},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "recent",
            "description": "List recent Mirror memories for an agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "default": DEFAULT_AGENT},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    ]


def handle_tool_call(name: str, args: dict) -> dict:
    if name == "remember":
        content = args["content"]
        agent_id = args.get("agent_id", DEFAULT_AGENT)
        body = {
            "text": content,
            "agent": agent_id,
            "context_id": args.get("context_id", f"{agent_id}-memory"),
            "metadata": args.get("metadata", {}),
        }
        result = mirror_request("POST", "/store", body)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    if name == "recall":
        body = {
            "query": args["query"],
            "agent_filter": args.get("agent_id"),
            "top_k": args.get("limit", 5),
        }
        result = mirror_request("POST", "/search", body)
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

    if name == "recent":
        agent_id = args.get("agent_id", DEFAULT_AGENT)
        params = urlencode({"limit": args.get("limit", 10)})
        result = mirror_request("GET", f"/recent/{agent_id}?{params}")
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}]}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            resp = make_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mirror", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = make_response(msg_id, {"tools": get_tools()})
        elif method == "tools/call":
            result = handle_tool_call(params.get("name", ""), params.get("arguments", {}))
            resp = make_response(msg_id, result)
        elif method == "ping":
            resp = make_response(msg_id, {})
        else:
            resp = make_response(msg_id, error=f"Unknown method: {method}")

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
