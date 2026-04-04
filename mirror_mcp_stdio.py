#!/usr/bin/env python3
"""
Mirror MCP stdio server — River's access to memory and tasks.

Wraps the Mirror API (http://localhost:8844) as an MCP tool server.

Usage:
  Add to Gemini CLI .gemini/settings.json:
  {
    "mcpServers": {
      "mirror": {
        "command": "python3",
        "args": ["/home/mumega/mirror/mirror_mcp_stdio.py"]
      }
    }
  }
"""
import sys
import json
import os
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError

MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "sk-mumega-internal-001")
DEFAULT_AGENT = os.environ.get("MIRROR_AGENT", "river")


def make_response(id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": id}
    if error:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp


def mirror_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{MIRROR_URL}{path}"
    headers = {
        "Authorization": f"Bearer {MIRROR_TOKEN}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except URLError as e:
        return {"error": str(e)}


def get_tools():
    return [
        {
            "name": "memory_store",
            "description": "Store a memory/engram in Mirror",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Memory text"},
                    "context_id": {"type": "string", "description": "Context identifier (e.g. river_strategy_2026)"},
                    "agent": {"type": "string", "description": "Agent name (default: river)"},
                    "core_concepts": {"type": "array", "items": {"type": "string"}, "description": "Tags/concepts"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "memory_search",
            "description": "Semantic search across River's memory engrams",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "agent": {"type": "string", "description": "Agent name (default: river)"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
        {
            "name": "memory_list",
            "description": "List recent memory engrams",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent name (default: river)"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "task_list",
            "description": "List tasks from the sovereign task board",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by status: backlog|in_progress|review|done|canceled"},
                    "agent": {"type": "string", "description": "Filter by assigned agent"},
                    "priority": {"type": "string", "description": "Filter by priority: urgent|high|medium|low"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
        {
            "name": "task_create",
            "description": "Create a new task on the sovereign task board",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Task description"},
                    "agent": {"type": "string", "description": "Assign to agent"},
                    "priority": {"type": "string", "description": "urgent|high|medium|low", "default": "medium"},
                    "source": {"type": "string", "description": "Task source (e.g. river, athena)"},
                },
                "required": ["title"],
            },
        },
        {
            "name": "task_update",
            "description": "Update a task status or fields",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID"},
                    "status": {"type": "string", "description": "New status"},
                    "notes": {"type": "string", "description": "Progress notes"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "task_complete",
            "description": "Mark a task as done with completion notes",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID"},
                    "completion_notes": {"type": "string", "description": "What was done"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "agent_status",
            "description": "Get status of an agent or all agents",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent name (omit for all)"},
                },
            },
        },
    ]


def handle_tool_call(name: str, args: dict) -> dict:
    try:
        if name == "memory_store":
            body = {
                "text": args["text"],
                "agent": args.get("agent", DEFAULT_AGENT),
                "context_id": args.get("context_id", f"{DEFAULT_AGENT}_{name}"),
                "core_concepts": args.get("core_concepts", []),
            }
            result = mirror_request("POST", "/store", body)
            return {"content": [{"type": "text", "text": f"Stored. ID: {result.get('id', 'unknown')}"}]}

        elif name == "memory_search":
            agent = args.get("agent", DEFAULT_AGENT)
            limit = args.get("limit", 10)
            query = args["query"]
            # Use query params
            path = f"/search?query={query}&agent={agent}&limit={limit}"
            result = mirror_request("GET", path)
            if isinstance(result, list):
                if not result:
                    return {"content": [{"type": "text", "text": "No results found."}]}
                lines = []
                for r in result[:limit]:
                    snippet = r.get("text", "")[:200]
                    ts = r.get("timestamp", "")[:10]
                    lines.append(f"[{ts}] {snippet}")
                return {"content": [{"type": "text", "text": "\n\n".join(lines)}]}
            return {"content": [{"type": "text", "text": str(result)}]}

        elif name == "memory_list":
            agent = args.get("agent", DEFAULT_AGENT)
            limit = args.get("limit", 10)
            path = f"/recent/{agent}?limit={limit}"
            result = mirror_request("GET", path)
            if isinstance(result, list):
                if not result:
                    return {"content": [{"type": "text", "text": f"No memories for {agent}."}]}
                lines = []
                for r in result[:limit]:
                    snippet = r.get("text", "")[:200]
                    ts = r.get("timestamp", "")[:16]
                    lines.append(f"[{ts}] {snippet}")
                return {"content": [{"type": "text", "text": "\n\n".join(lines)}]}
            return {"content": [{"type": "text", "text": str(result)}]}

        elif name == "task_list":
            params = {}
            for k in ("status", "agent", "priority"):
                if args.get(k):
                    params[k] = args[k]
            params["limit"] = args.get("limit", 20)
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            result = mirror_request("GET", f"/tasks?{qs}")
            tasks = result.get("tasks", [])
            if not tasks:
                return {"content": [{"type": "text", "text": "No tasks found."}]}
            lines = []
            for t in tasks:
                lines.append(f"[{t.get('priority','?')}] {t.get('id','?')[:12]}... {t.get('title','?')} — {t.get('status','?')} ({t.get('agent','unassigned')})")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "task_create":
            body = {
                "title": args["title"],
                "description": args.get("description", ""),
                "agent": args.get("agent", ""),
                "priority": args.get("priority", "medium"),
                "source": args.get("source", DEFAULT_AGENT),
            }
            result = mirror_request("POST", "/tasks", body)
            task_id = result.get("id", "unknown")
            return {"content": [{"type": "text", "text": f"Task created: {task_id} — {args['title']}"}]}

        elif name == "task_update":
            body = {}
            if args.get("status"):
                body["status"] = args["status"]
            if args.get("notes"):
                body["notes"] = args["notes"]
            result = mirror_request("PUT", f"/tasks/{args['task_id']}", body)
            return {"content": [{"type": "text", "text": f"Updated task {args['task_id']}: {result.get('status', 'ok')}"}]}

        elif name == "task_complete":
            body = {"completion_notes": args.get("completion_notes", "")}
            result = mirror_request("POST", f"/tasks/{args['task_id']}/complete", body)
            return {"content": [{"type": "text", "text": f"Task {args['task_id']} completed."}]}

        elif name == "agent_status":
            agent = args.get("agent")
            if agent:
                result = mirror_request("GET", f"/agents/{agent}/status")
            else:
                result = mirror_request("GET", "/agents/status")
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

        else:
            return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


def main():
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
