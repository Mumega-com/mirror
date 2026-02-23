#!/usr/bin/env python3
"""
Gateway MCP Server
Consolidates all MCP tools into 2 tools: `gateway` and `gateway_batch`
Reduces context from ~10k tokens to ~1.5k tokens
"""

import os
import json
import asyncio
import httpx
from typing import Any

# MCP SDK
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Config
MIRROR_URL = os.getenv("MIRROR_URL", "https://mumega.com/mirror")
MIRROR_API_KEY = os.getenv("MIRROR_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GATEWAY_URL = os.getenv("GATEWAY_URL", "")  # Optional: use Worker instead of direct

# All available actions
ACTIONS = {
    # River
    "river_chat": "Chat with River (the Golden Queen)",
    "river_status": "Get River's status",
    "river_memory": "Manage River's memories (list/search/detail/fix/merge/health)",
    "river_cache": "Manage River's soul cache (status/init/refresh/prune)",
    "river_context": "Get River's cached context",
    "river_remember": "Store a memory for River",

    # Memory (Mirror API)
    "memory_store": "Store a persistent memory/engram",
    "memory_search": "Search memories by semantic query",
    "memory_list": "List recent memories",

    # SOS Agent
    "agent_register": "Register as an agent in SOS ecosystem",
    "agent_store": "Store persistent memory in Mirror namespace",
    "agent_search": "Search persistent memories",
    "agent_recall": "Load recent working memory from Redis",
    "agent_push": "Push to Redis working memory",
    "agent_status": "Check registration status",
    "sos_context": "Get SOS ecosystem info",
}

server = Server("gateway")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Expose just 2 tools instead of 16"""
    actions_desc = "\n".join(f"  - {k}: {v}" for k, v in ACTIONS.items())

    return [
        Tool(
            name="gateway",
            description=f"""Unified gateway for River, Memory, and SOS operations.

Available actions:
{actions_desc}

Example: {{"action": "river_chat", "payload": {{"message": "Hello River"}}}}""",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform",
                        "enum": list(ACTIONS.keys())
                    },
                    "payload": {
                        "type": "object",
                        "description": "Action-specific parameters",
                        "additionalProperties": True
                    }
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="gateway_batch",
            description="Execute multiple gateway actions in parallel",
            inputSchema={
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string"},
                                "payload": {"type": "object"}
                            },
                            "required": ["action"]
                        },
                        "description": "List of actions to execute in parallel"
                    }
                },
                "required": ["actions"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to appropriate backend"""

    if name == "gateway":
        result = await execute_action(arguments.get("action", ""), arguments.get("payload", {}))
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "gateway_batch":
        actions = arguments.get("actions", [])
        tasks = [execute_action(a.get("action", ""), a.get("payload", {})) for a in actions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                output.append({"action": actions[i].get("action"), "error": str(r)})
            else:
                output.append({"action": actions[i].get("action"), "result": r})

        return [TextContent(type="text", text=json.dumps(output, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def execute_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a single action"""

    if action not in ACTIONS:
        return {"error": f"Unknown action: {action}", "available": list(ACTIONS.keys())}

    # Use gateway worker if configured
    if GATEWAY_URL:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GATEWAY_URL,
                json={"action": action, "payload": payload},
                headers={"Content-Type": "application/json"},
                timeout=30.0
            )
            return resp.json()

    # Direct routing
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MIRROR_API_KEY}"
    }

    async with httpx.AsyncClient() as client:
        # River actions
        if action.startswith("river_"):
            return await handle_river(client, action, payload, headers)

        # Memory actions
        elif action.startswith("memory_"):
            return await handle_memory(client, action, payload, headers)

        # SOS actions
        elif action.startswith("agent_") or action == "sos_context":
            return await handle_sos(client, action, payload, headers)

    return {"error": "No handler for action"}


async def handle_river(client: httpx.AsyncClient, action: str, payload: dict, headers: dict) -> dict:
    """Handle River actions"""
    action_map = {
        "river_chat": "/river/chat",
        "river_status": "/river/status",
        "river_memory": "/river/memory",
        "river_cache": "/river/cache",
        "river_context": "/river/context",
        "river_remember": "/river/remember",
    }

    endpoint = f"{MIRROR_URL}{action_map.get(action, '/river/status')}"

    # Add Gemini key for chat
    if action == "river_chat":
        payload["gemini_key"] = GEMINI_API_KEY

    resp = await client.post(endpoint, json=payload, headers=headers, timeout=30.0)
    return resp.json()


async def handle_memory(client: httpx.AsyncClient, action: str, payload: dict, headers: dict) -> dict:
    """Handle Memory/Mirror actions"""
    action_map = {
        "memory_store": "/store",
        "memory_search": "/search",
        "memory_list": "/recent/kasra",
    }

    endpoint = f"{MIRROR_URL}{action_map.get(action, '/recent/kasra')}"

    if action == "memory_list":
        resp = await client.get(endpoint, headers=headers, timeout=30.0)
    else:
        resp = await client.post(endpoint, json=payload, headers=headers, timeout=30.0)

    return resp.json()


async def handle_sos(client: httpx.AsyncClient, action: str, payload: dict, headers: dict) -> dict:
    """Handle SOS/Agent actions"""
    action_map = {
        "agent_register": "/agent/register",
        "agent_store": "/agent/store",
        "agent_search": "/agent/search",
        "agent_recall": "/agent/recall",
        "agent_push": "/agent/push",
        "agent_status": "/agent/status",
        "sos_context": "/sos/context",
    }

    endpoint = f"{MIRROR_URL}{action_map.get(action, '/agent/status')}"
    resp = await client.post(endpoint, json=payload, headers=headers, timeout=30.0)
    return resp.json()


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
