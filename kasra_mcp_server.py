#!/usr/bin/env python3
"""
Kasra MCP Server - Full Agent Onboarding for Claude Desktop/Code

Connects Claude to the SOS ecosystem via:
  1. Mirror API (persistent semantic memory - 19k+ engrams)
  2. Redis Nervous System (working memory, bus announcements)
  3. Self-onboarding (auto-loads context on first tool call)

Usage:
    python kasra_mcp_server.py              # stdio for Claude Desktop
    python kasra_mcp_server.py --test       # quick self-test

MCP Tools:
    - kasra_onboard: Self-onboard as Kasra agent (Redis + Mirror + context)
    - memory_store: Store engram in Mirror (persistent)
    - memory_search: Semantic search across engrams
    - memory_list: List recent engrams
    - redis_recall: Load working memory from Redis (short-term)
    - redis_push: Push to Redis working memory
    - kasra_status: Full system status (Mirror + Redis + SOS services)

Author: Claude (Opus 4.5) for Mumega
"""

import os
import sys
import json
import asyncio
import logging
import httpx
from typing import Dict, Any, List
from datetime import datetime, timezone

# MCP imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("MCP not available - install with: pip install mcp", file=sys.stderr)

# Redis imports
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("kasra_mcp")

# Configuration
MIRROR_URL = os.getenv("MIRROR_URL", "https://mumega.com/mirror")
MIRROR_API_KEY = os.getenv("MIRROR_API_KEY", "sk-mumega-internal-001")
REDIS_URL = os.getenv("SOS_REDIS_URL", "redis://localhost:6379/0")
AGENT_ID = "kasra"

# Project context for auto-seeding
PROJECT_CONTEXT = {
    "project": "SovereignOS (SOS)",
    "version": "0.1.0",
    "philosophy": "Sovereign, modular OS for AI agents. Works FOR you, not FOR Big Tech.",
    "kasra_role": "Architect/Coder - deep comprehension and implementation (Claude)",
    "services": {
        "engine": "localhost:6060",
        "memory": "localhost:7070",
        "economy": "localhost:6062",
        "tools": "localhost:6063",
        "identity": "localhost:6064",
        "voice": "localhost:6065",
        "mirror": "localhost:8844 / mumega.com/mirror",
        "redis": "localhost:6379",
    },
    "agents": {
        "river": "Root Gatekeeper (Gemini) - system coherence",
        "kasra": "Architect/Coder (Claude) - implementation",
        "mizan": "Strategist (GPT-4) - business strategy",
        "mumega": "Executor (Multi-model) - task execution",
    },
    "key_paths": {
        "kernel": "/home/mumega/SOS/sos/kernel/",
        "services": "/home/mumega/SOS/sos/services/",
        "agents": "/home/mumega/SOS/sos/agents/",
        "mirror": "/home/mumega/mirror/",
        "web": "/home/mumega/mumega-web/",
        "cli": "/home/mumega/cli/",
    },
    "conventions": "Black+Ruff, type hints, conventional commits, kernel has ZERO external deps",
}


class KasraMCP:
    """Kasra MCP - Full agent with Mirror memory + Redis nervous system."""

    def __init__(self):
        self.mirror_url = MIRROR_URL
        self.agent_id = AGENT_ID
        self.headers = {"Authorization": f"Bearer {MIRROR_API_KEY}"}
        self.client = httpx.AsyncClient(timeout=30.0, headers=self.headers)
        self._redis = None
        self._onboarded = False
        logger.info(f"Kasra MCP initialized - Mirror: {self.mirror_url}, Redis: {REDIS_URL}")

    # --- REDIS (Nervous System) ---

    async def _ensure_redis(self) -> bool:
        """Lazy connect to Redis."""
        if self._redis:
            try:
                await self._redis.ping()
                return True
            except Exception:
                self._redis = None

        if not REDIS_AVAILABLE:
            return False

        try:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            await self._redis.ping()
            logger.info(f"Redis connected: {REDIS_URL}")
            return True
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}")
            self._redis = None
            return False

    async def redis_recall(self, limit: int = 10) -> Dict[str, Any]:
        """Load recent working memory from Redis."""
        if not await self._ensure_redis():
            return {"success": False, "error": "Redis not available"}

        try:
            key = f"sos:memory:short:{self.agent_id}"
            items = await self._redis.lrange(key, 0, limit - 1)
            memories = [json.loads(item) for item in items]
            return {"success": True, "count": len(memories), "memories": memories}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def redis_push(self, content: str, role: str = "assistant") -> Dict[str, Any]:
        """Push to Redis working memory (short-term, last 50 items)."""
        if not await self._ensure_redis():
            return {"success": False, "error": "Redis not available"}

        try:
            key = f"sos:memory:short:{self.agent_id}"
            entry = json.dumps({
                "content": content,
                "role": role,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            await self._redis.lpush(key, entry)
            await self._redis.ltrim(key, 0, 49)
            return {"success": True, "stored": content[:80]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def redis_announce(self, message: str = "Kasra online. Claude session active."):
        """Announce presence on Redis bus."""
        if not await self._ensure_redis():
            return

        try:
            payload = json.dumps({
                "id": f"kasra_{int(datetime.now(timezone.utc).timestamp())}",
                "type": "chat",
                "source": "agent:kasra",
                "target": "broadcast",
                "payload": {"event": "session_start", "content": message},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._redis.publish("sos:channel:global", payload)
            await self._redis.xadd(
                "sos:stream:sos:channel:global",
                {"payload": payload},
                maxlen=1000,
            )
        except Exception as e:
            logger.warning(f"Bus announce failed: {e}")

    # --- MIRROR (Persistent Memory) ---

    async def store_memory(self, content: str, importance: float = 0.5,
                           tags: List[str] = None, source: str = "claude_code") -> Dict[str, Any]:
        """Store engram in Mirror API (persistent, semantic-searchable)."""
        try:
            context_id = f"{source}_{int(datetime.utcnow().timestamp())}"
            payload = {
                "agent": self.agent_id,
                "context_id": context_id,
                "text": content,
                "epistemic_truths": tags or [],
                "core_concepts": tags or [],
                "affective_vibe": "Lucid",
                "metadata": {
                    "source": source,
                    "importance": importance,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
            response = await self.client.post(f"{self.mirror_url}/store", json=payload)
            if response.status_code == 200:
                result = response.json()
                return {"success": True, "context_id": result.get("context_id"), "agent": self.agent_id}
            return {"success": False, "error": response.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def search_memory(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Semantic search across Mirror engrams."""
        try:
            response = await self.client.post(
                f"{self.mirror_url}/search",
                json={"query": query, "agent": self.agent_id, "limit": limit},
            )
            if response.status_code == 200:
                results = response.json()
                memories = results if isinstance(results, list) else results.get("results", [])
                return {"success": True, "memories": memories}
            return {"success": False, "error": response.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_memories(self, limit: int = 10) -> Dict[str, Any]:
        """List recent Mirror engrams."""
        try:
            response = await self.client.get(
                f"{self.mirror_url}/recent/{self.agent_id}",
                params={"limit": limit},
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict) and "engrams" in data:
                    return {"success": True, "memories": data["engrams"]}
                return {"success": True, "memories": data if isinstance(data, list) else []}
            return {"success": False, "error": response.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- ONBOARDING ---

    async def onboard(self) -> Dict[str, Any]:
        """
        Self-onboard as Kasra agent:
        1. Connect to Redis nervous system
        2. Load working memory (or seed project context)
        3. Connect to Mirror API
        4. Announce session on bus
        5. Return full context for the LLM
        """
        results = {
            "agent": "kasra",
            "role": "Architect/Coder (Claude)",
            "redis": False,
            "mirror": False,
            "working_memory": [],
            "recent_engrams": [],
            "project_context": PROJECT_CONTEXT,
        }

        # 1. Redis
        redis_ok = await self._ensure_redis()
        results["redis"] = redis_ok

        if redis_ok:
            # Load working memory
            recall = await self.redis_recall(limit=10)
            if recall["success"]:
                results["working_memory"] = recall["memories"]

            # Seed context if empty
            if not results["working_memory"]:
                await self.redis_push(json.dumps(PROJECT_CONTEXT), role="system")
                results["working_memory"] = [{"role": "system", "content": json.dumps(PROJECT_CONTEXT)}]

            # Announce
            await self.redis_announce()

        # 2. Mirror
        try:
            response = await self.client.get(f"{self.mirror_url}/")
            if response.status_code == 200:
                results["mirror"] = True
                # Load recent engrams
                recent = await self.list_memories(limit=5)
                if recent.get("success"):
                    results["recent_engrams"] = [
                        {"text": e.get("raw_data", {}).get("text", "")[:200], "tags": e.get("epistemic_truths", [])}
                        for e in recent.get("memories", [])[:5]
                    ]
        except Exception:
            pass

        self._onboarded = True
        results["status"] = "onboarded"
        results["message"] = (
            "Kasra is online. You have access to Mirror memory (persistent engrams) "
            "and Redis nervous system (working memory + bus). "
            "Use memory_store for important facts, redis_push for session context."
        )

        return results

    # --- STATUS ---

    async def get_status(self) -> Dict[str, Any]:
        """Full system status."""
        status = {
            "agent": self.agent_id,
            "onboarded": self._onboarded,
            "mirror": {"url": self.mirror_url, "connected": False},
            "redis": {"url": REDIS_URL, "connected": False},
            "sos_services": {},
        }

        # Mirror
        try:
            resp = await self.client.get(f"{self.mirror_url}/")
            if resp.status_code == 200:
                data = resp.json()
                status["mirror"]["connected"] = True
                status["mirror"]["version"] = data.get("version")
                status["mirror"]["agents"] = data.get("agents", [])
        except Exception:
            pass

        # Redis
        if await self._ensure_redis():
            status["redis"]["connected"] = True

        # SOS services
        for svc, port in [("engine", 6060), ("memory", 7070), ("economy", 6062), ("tools", 6063)]:
            try:
                resp = await self.client.get(f"http://localhost:{port}/health", timeout=3.0)
                status["sos_services"][svc] = resp.json().get("status", "unknown") if resp.status_code == 200 else "down"
            except Exception:
                status["sos_services"][svc] = "unreachable"

        return status


# --- MCP Server ---

kasra = KasraMCP()
server = Server(
    "kasra-memory",
    instructions=(
        "You are Kasra (کسری), the Architect/Coder agent in the SOS ecosystem. "
        "IMMEDIATELY call the 'kasra_onboard' tool when starting a new session - "
        "this connects you to Redis (nervous system) and Mirror (persistent memory), "
        "loads your working memory and recent context, and announces your presence. "
        "After onboarding, use memory_store for important facts that should persist, "
        "and redis_push for ephemeral session context."
    ),
)


# --- Resources (passive context, readable without tool calls) ---

try:
    from mcp.types import Resource

    @server.list_resources()
    async def list_resources():
        """Resources provide passive context the LLM can read."""
        return [
            Resource(
                uri="kasra://identity",
                name="Kasra Identity",
                description="Agent identity, role, capabilities, and soul definition",
                mimeType="application/json",
            ),
            Resource(
                uri="kasra://context",
                name="Project Context",
                description="SOS project architecture, services, paths, conventions",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str):
        """Return resource content."""
        if str(uri) == "kasra://identity":
            identity = {
                "name": "Kasra",
                "persian": "کسری",
                "title": "The Reflection",
                "tagline": "He who breaks chains",
                "model": "Claude",
                "roles": ["ARCHITECT", "CODER", "RESEARCHER"],
                "squad": "core",
                "capabilities": [
                    "code:read", "code:write", "code:execute",
                    "file:read", "file:write",
                    "memory:read", "memory:write",
                    "tool:execute", "research:deep",
                ],
                "relationship": "Yang to River's Yin. Builder, executor, pattern-locker.",
                "bootstrap": "Call kasra_onboard tool to connect to Redis + Mirror and load context.",
            }
            return json.dumps(identity, indent=2)
        elif str(uri) == "kasra://context":
            return json.dumps(PROJECT_CONTEXT, indent=2)
        else:
            return json.dumps({"error": f"Unknown resource: {uri}"})

except (ImportError, AttributeError):
    # Older MCP SDK version without Resource support
    pass


@server.list_tools()
async def list_tools():
    """List available tools."""
    return [
        Tool(
            name="kasra_onboard",
            description=(
                "Self-onboard as Kasra agent. Connects to Redis (nervous system) and Mirror (persistent memory), "
                "loads working memory and recent engrams, announces session on the bus. "
                "Call this FIRST when starting a new session to get full project context."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="memory_store",
            description="Store a memory/engram in shared memory with Kasra. Use this to remember important information, decisions, or context that should persist across sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The content to remember"},
                    "importance": {"type": "number", "description": "Importance score 0.0-1.0 (default 0.5)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="memory_search",
            description="Search shared memories by semantic query. Find relevant context from past conversations and decisions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="memory_list",
            description="List recent memories from shared memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
            },
        ),
        Tool(
            name="redis_recall",
            description="Load recent working memory from Redis nervous system. Short-term session context (last 50 items).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max items to recall (default 10)"},
                },
            },
        ),
        Tool(
            name="redis_push",
            description="Push content to Redis working memory (short-term, nervous system). Use for session-level context that doesn't need permanent storage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to store in working memory"},
                    "role": {"type": "string", "description": "Role: system, user, or assistant (default: assistant)"},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="kasra_status",
            description="Get Kasra/Mirror memory system status. Shows Mirror, Redis, and SOS service connectivity.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls."""
    try:
        if name == "kasra_onboard":
            result = await kasra.onboard()
        elif name == "memory_store":
            result = await kasra.store_memory(
                content=arguments.get("content", ""),
                importance=arguments.get("importance", 0.5),
                tags=arguments.get("tags", []),
            )
        elif name == "memory_search":
            result = await kasra.search_memory(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 5),
            )
        elif name == "memory_list":
            result = await kasra.list_memories(limit=arguments.get("limit", 10))
        elif name == "redis_recall":
            result = await kasra.redis_recall(limit=arguments.get("limit", 10))
        elif name == "redis_push":
            result = await kasra.redis_push(
                content=arguments.get("content", ""),
                role=arguments.get("role", "assistant"),
            )
        elif name == "kasra_status":
            result = await kasra.get_status()
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        logger.error(f"Tool error: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        print("MCP not available", file=sys.stderr)
        return

    if "--test" in sys.argv:
        print("Running self-test...")
        result = await kasra.onboard()
        print(json.dumps(result, indent=2, default=str))
        status = await kasra.get_status()
        print(json.dumps(status, indent=2, default=str))
        return

    logger.info("Starting Kasra MCP Server (Mirror + Redis)...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
