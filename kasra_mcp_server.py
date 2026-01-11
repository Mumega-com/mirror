#!/usr/bin/env python3
"""
Kasra MCP Server - Shared Memory with Claude Code

This MCP server allows Claude Code to share memory with Kasra
through the Mirror API. Both can store and retrieve memories.

Usage:
    python kasra_mcp_server.py

MCP Tools provided:
    - memory_store: Store a memory/engram
    - memory_search: Search memories by query
    - memory_list: List recent memories
    - kasra_status: Get Kasra's status

Author: Claude (Opus 4.5) for Kay Hermes
Date: 2026-01-09
"""

import os
import sys
import json
import asyncio
import logging
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

# MCP imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("MCP not available - install with: pip install mcp", file=sys.stderr)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kasra_mcp")

# Mirror API endpoint
MIRROR_URL = os.getenv("MIRROR_URL", "https://mumega.com/mirror")
AGENT_ID = "kasra"  # Shared agent ID for Kasra + Claude Code


class KasraMCP:
    """Kasra MCP Server - Shared memory with Claude Code."""

    def __init__(self):
        self.mirror_url = MIRROR_URL
        self.agent_id = AGENT_ID
        self.client = httpx.AsyncClient(timeout=30.0)
        logger.info(f"Kasra MCP initialized - Mirror: {self.mirror_url}")

    async def store_memory(self, content: str, importance: float = 0.5,
                           tags: List[str] = None, source: str = "claude_code") -> Dict[str, Any]:
        """Store a memory in Mirror for both Kasra and Claude Code."""
        try:
            payload = {
                "agent_id": self.agent_id,
                "content": content,
                "importance": importance,
                "tags": tags or [],
                "metadata": {
                    "source": source,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }

            response = await self.client.post(
                f"{self.mirror_url}/store",
                json=payload
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Stored memory: {content[:50]}...")
                return {"success": True, "engram_id": result.get("engram_id")}
            else:
                return {"success": False, "error": response.text}

        except Exception as e:
            logger.error(f"Error storing memory: {e}")
            return {"success": False, "error": str(e)}

    async def search_memory(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Search memories by semantic query."""
        try:
            response = await self.client.post(
                f"{self.mirror_url}/search",
                json={
                    "query": query,
                    "agent_id": self.agent_id,
                    "limit": limit
                }
            )

            if response.status_code == 200:
                results = response.json()
                return {"success": True, "memories": results.get("results", [])}
            else:
                return {"success": False, "error": response.text}

        except Exception as e:
            logger.error(f"Error searching memory: {e}")
            return {"success": False, "error": str(e)}

    async def list_memories(self, limit: int = 10) -> Dict[str, Any]:
        """List recent memories."""
        try:
            response = await self.client.get(
                f"{self.mirror_url}/engrams/{self.agent_id}",
                params={"limit": limit}
            )

            if response.status_code == 200:
                return {"success": True, "memories": response.json()}
            else:
                return {"success": False, "error": response.text}

        except Exception as e:
            logger.error(f"Error listing memories: {e}")
            return {"success": False, "error": str(e)}

    async def get_status(self) -> Dict[str, Any]:
        """Get Mirror/Kasra status."""
        try:
            response = await self.client.get(f"{self.mirror_url}/")
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "mirror_status": data.get("status"),
                    "agents": data.get("agents", []),
                    "version": data.get("version"),
                    "kasra_connected": self.agent_id in data.get("agents", [])
                }
            else:
                return {"success": False, "error": response.text}
        except Exception as e:
            return {"success": False, "error": str(e)}


# MCP Server setup
kasra = KasraMCP()
server = Server("kasra-memory")


@server.list_tools()
async def list_tools():
    """List available tools."""
    return [
        Tool(
            name="memory_store",
            description="Store a memory/engram in shared memory with Kasra. Use this to remember important information, decisions, or context that should persist across sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to remember"
                    },
                    "importance": {
                        "type": "number",
                        "description": "Importance score 0.0-1.0 (default 0.5)"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorization"
                    }
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="memory_search",
            description="Search shared memories by semantic query. Find relevant context from past conversations and decisions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 5)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_list",
            description="List recent memories from shared memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)"
                    }
                }
            }
        ),
        Tool(
            name="kasra_status",
            description="Get Kasra/Mirror memory system status.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls."""
    try:
        if name == "memory_store":
            result = await kasra.store_memory(
                content=arguments.get("content", ""),
                importance=arguments.get("importance", 0.5),
                tags=arguments.get("tags", [])
            )
        elif name == "memory_search":
            result = await kasra.search_memory(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 5)
            )
        elif name == "memory_list":
            result = await kasra.list_memories(
                limit=arguments.get("limit", 10)
            )
        elif name == "kasra_status":
            result = await kasra.get_status()
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        logger.error(f"Tool error: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        print("MCP not available", file=sys.stderr)
        return

    logger.info("Starting Kasra MCP Server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
