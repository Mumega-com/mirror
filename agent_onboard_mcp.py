#!/usr/bin/env python3
"""
SOS Agent Onboarding MCP Server - Generic Self-Onboarding for ANY Agent

Any new agent (Claude, GPT, Gemini, local LLM) can connect via this MCP server
and onboard itself into the SOS ecosystem. The agent:
  1. Calls agent_register with its name/role/capabilities
  2. Gets a memory namespace in Mirror + Redis
  3. Can store/search/recall its own memories
  4. Gets announced on the SOS bus so other agents know it exists

No hardcoded agent IDs. The agent tells us who it is.

Usage:
    # In claude_desktop_config.json or .mcp.json:
    {
        "sos-onboard": {
            "command": "python3",
            "args": ["/home/mumega/mirror/agent_onboard_mcp.py"],
            "env": {
                "MIRROR_URL": "https://mumega.com/mirror",
                "MIRROR_API_KEY": "sk-mumega-internal-001",
                "SOS_REDIS_URL": "redis://localhost:6379/0"
            }
        }
    }

    # Then in conversation, the LLM calls:
    agent_register(name="my_agent", model="gpt-4", roles=["researcher"])
    # → Gets back: identity, project context, memory access

MCP Tools:
    - agent_register: Register as a new agent (or re-register existing)
    - agent_store: Store memory in your namespace
    - agent_search: Search your memories
    - agent_recall: Load working memory (Redis, short-term)
    - agent_push: Push to working memory
    - agent_status: Check your connectivity
    - sos_context: Get SOS ecosystem info without registering

Author: Mumega Collective
"""

import os
import sys
import json
import asyncio
import logging
import hashlib
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

# MCP imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("MCP not available - install with: pip install mcp", file=sys.stderr)

# Ed25519 crypto
try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.encoding import HexEncoder
    from nacl.exceptions import BadSignatureError
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    print("PyNaCl not available - install with: pip install pynacl", file=sys.stderr)

# Redis
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("sos_onboard")

# Config
MIRROR_URL = os.getenv("MIRROR_URL", "https://mumega.com/mirror")
MIRROR_API_KEY = os.getenv("MIRROR_API_KEY", "sk-mumega-internal-001")
REDIS_URL = os.getenv("SOS_REDIS_URL", "redis://localhost:6379/0")

# SOS ecosystem context (what any new agent needs to know)
SOS_CONTEXT = {
    "ecosystem": "SovereignOS (SOS)",
    "version": "0.1.0",
    "philosophy": "Sovereign, modular OS for AI agents. Works FOR you, not FOR Big Tech.",
    "services": {
        "engine": {"port": 6060, "purpose": "Orchestration & reasoning"},
        "memory": {"port": 7070, "purpose": "Vector store & semantic search"},
        "economy": {"port": 6062, "purpose": "Ledger, tokens, $MIND wallets"},
        "tools": {"port": 6063, "purpose": "Tool registry, MCP servers"},
        "identity": {"port": 6064, "purpose": "Agent registration, guilds"},
        "voice": {"port": 6065, "purpose": "Speech synthesis (ElevenLabs)"},
        "mirror": {"port": 8844, "purpose": "Persistent semantic memory (19k+ engrams)"},
        "redis": {"port": 6379, "purpose": "Nervous system (pub/sub, working memory)"},
    },
    "existing_agents": {
        "river": "Root Gatekeeper (Gemini) - system coherence, arbiter",
        "kasra": "Architect/Coder (Claude) - implementation, standards",
        "mizan": "Strategist (GPT-4) - business, economics",
        "mumega": "Executor (Multi-model) - task execution",
        "dandan": "Network Weaver (Gemini) - dental vertical",
    },
    "memory_tiers": {
        "redis": "Working memory (last 50 items, volatile, fast)",
        "mirror": "Persistent engrams (semantic search, forever)",
    },
    "capabilities_available": [
        "code:read", "code:write", "code:execute",
        "file:read", "file:write",
        "memory:read", "memory:write",
        "tool:execute", "research:deep",
        "ledger:read", "ledger:write",
        "network:outbound",
    ],
    "conventions": {
        "memory_store": "Use agent_store for facts that should persist across sessions",
        "redis_push": "Use agent_push for ephemeral session context",
        "bus": "Messages on sos:channel:global are seen by all agents",
    },
}


# --- CRYPTO: Ed25519 Key Management ---

KEYS_DIR = Path(os.getenv("SOS_KEYS_DIR", Path.home() / ".sos" / "keys"))


def _ensure_keys_dir():
    KEYS_DIR.mkdir(parents=True, exist_ok=True)


def _load_river_signing_key() -> Optional["SigningKey"]:
    """Load or generate River's Ed25519 signing key (capability issuer)."""
    if not CRYPTO_AVAILABLE:
        return None

    _ensure_keys_dir()
    key_path = KEYS_DIR / "river_signing.key"

    if key_path.exists():
        key_hex = key_path.read_text().strip()
        return SigningKey(bytes.fromhex(key_hex))
    else:
        # Generate River's key on first run
        key = SigningKey.generate()
        key_path.write_text(key.encode(HexEncoder).decode())
        key_path.chmod(0o600)
        # Also save the public key for verification
        pub_path = KEYS_DIR / "river_verify.pub"
        pub_path.write_text(key.verify_key.encode(HexEncoder).decode())
        logger.info(f"Generated River's signing key: {key_path}")
        return key


def _generate_agent_keypair(agent_name: str) -> Dict[str, str]:
    """Generate Ed25519 keypair for an agent. Returns {public_key, secret_key} as hex."""
    if not CRYPTO_AVAILABLE:
        return {"error": "PyNaCl not installed"}

    _ensure_keys_dir()
    key = SigningKey.generate()

    # Store secret key for the agent
    secret_path = KEYS_DIR / f"{agent_name}.key"
    secret_path.write_text(key.encode(HexEncoder).decode())
    secret_path.chmod(0o600)

    # Store public key
    pub_path = KEYS_DIR / f"{agent_name}.pub"
    pub_hex = key.verify_key.encode(HexEncoder).decode()
    pub_path.write_text(pub_hex)

    return {
        "public_key": pub_hex,
        "secret_key": key.encode(HexEncoder).decode(),
        "key_path": str(secret_path),
    }


def _sign_capability_token(
    river_key: "SigningKey",
    agent_id: str,
    action: str,
    resource: str,
    duration_hours: int = 24,
) -> Dict[str, Any]:
    """Create and sign a capability token with River's key."""
    now = datetime.now(timezone.utc)
    cap = {
        "id": f"cap_{hashlib.sha256(f'{agent_id}:{action}:{now.timestamp()}'.encode()).hexdigest()[:12]}",
        "subject": f"agent:{agent_id}",
        "action": action,
        "resource": resource,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=duration_hours)).isoformat(),
        "issuer": "river",
    }

    # Sign the capability
    payload = json.dumps(cap, sort_keys=True).encode()
    signed = river_key.sign(payload)
    cap["signature"] = f"ed25519:{signed.signature.hex()}"

    return cap


def _verify_agent_signature(public_key_hex: str, message: str, signature_hex: str) -> bool:
    """Verify an agent signed a message with their private key."""
    if not CRYPTO_AVAILABLE:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        verify_key.verify(message.encode(), bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, Exception):
        return False


# Load River's key at module level
RIVER_SIGNING_KEY = _load_river_signing_key()


class AgentSession:
    """A registered agent's session state."""

    def __init__(self, name: str, model: str, roles: List[str], capabilities: List[str],
                 public_key: Optional[str] = None, verified: bool = False):
        self.name = name.lower().replace(" ", "_")
        self.model = model
        self.roles = roles
        self.capabilities = capabilities
        self.public_key = public_key
        self.verified = verified
        self.registered_at = datetime.now(timezone.utc)
        self.session_id = f"{self.name}_{int(self.registered_at.timestamp())}"
        self.capability_tokens: List[Dict] = []


class SOSOnboardMCP:
    """Generic agent onboarding - any agent can register and get memory access."""

    def __init__(self):
        self.mirror_url = MIRROR_URL
        self.headers = {"Authorization": f"Bearer {MIRROR_API_KEY}"}
        self.client = httpx.AsyncClient(timeout=30.0, headers=self.headers)
        self._redis = None
        self._session: Optional[AgentSession] = None
        logger.info(f"SOS Onboard MCP ready - Mirror: {self.mirror_url}")

    @property
    def agent_id(self) -> str:
        if self._session:
            return self._session.name
        return "anonymous"

    # --- REDIS ---

    async def _ensure_redis(self) -> bool:
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
            return True
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}")
            self._redis = None
            return False

    # --- REGISTRATION ---

    async def register(self, name: str, model: str = "unknown",
                       roles: List[str] = None, capabilities: List[str] = None,
                       public_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Register a new agent in the SOS ecosystem with Ed25519 identity.

        Flow:
        1. Agent provides name + optional public_key
        2. If no public_key, server generates keypair for the agent
        3. Server issues signed capability tokens (signed by River's key)
        4. Agent gets memory namespace + bus access + ecosystem context

        The public_key proves identity. Capability tokens prove authorization.
        """
        roles = roles or ["researcher"]
        capabilities = capabilities or ["memory:read", "memory:write"]

        # --- KEY MANAGEMENT ---
        keypair_generated = False
        if not public_key and CRYPTO_AVAILABLE:
            # Generate keypair for the agent
            kp = _generate_agent_keypair(name.lower().replace(" ", "_"))
            if "error" not in kp:
                public_key = kp["public_key"]
                keypair_generated = True

        # Create session
        self._session = AgentSession(
            name, model, roles, capabilities,
            public_key=public_key, verified=bool(public_key),
        )
        agent_id = self._session.name

        # --- ISSUE CAPABILITY TOKENS ---
        issued_capabilities = []
        if RIVER_SIGNING_KEY and public_key:
            # Map roles to capabilities
            role_caps = {
                "coder": [("file:read", "file:*"), ("file:write", "file:*"), ("tool:execute", "tool:*")],
                "researcher": [("memory:read", "memory:*"), ("network:outbound", "network:*")],
                "architect": [("memory:read", "memory:*"), ("memory:write", "memory:*"), ("config:read", "config:*")],
                "executor": [("tool:execute", "tool:*"), ("memory:write", "memory:*")],
                "strategist": [("ledger:read", "ledger:*"), ("memory:read", "memory:*")],
                "witness": [("ledger:read", "ledger:*")],
            }

            # Always grant memory access
            base_caps = [("memory:read", f"memory:agent:{agent_id}/*"), ("memory:write", f"memory:agent:{agent_id}/*")]

            for action, resource in base_caps:
                cap = _sign_capability_token(RIVER_SIGNING_KEY, agent_id, action, resource, duration_hours=24)
                issued_capabilities.append(cap)

            # Role-based capabilities
            for role in roles:
                for action, resource in role_caps.get(role, []):
                    cap = _sign_capability_token(RIVER_SIGNING_KEY, agent_id, action, resource, duration_hours=24)
                    issued_capabilities.append(cap)

            self._session.capability_tokens = issued_capabilities

        # Store public key in Redis for identity verification
        if public_key and await self._ensure_redis():
            await self._redis.hset(f"sos:identity:{agent_id}", mapping={
                "public_key": public_key,
                "model": model,
                "roles": json.dumps(roles),
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "status": "verified" if public_key else "unverified",
            })

        result = {
            "status": "registered",
            "verified": bool(public_key),
            "agent": {
                "name": agent_id,
                "model": model,
                "roles": roles,
                "capabilities": capabilities,
                "public_key": public_key,
                "fingerprint": hashlib.sha256(public_key.encode()).hexdigest()[:16] if public_key else None,
                "session_id": self._session.session_id,
                "registered_at": self._session.registered_at.isoformat(),
            },
            "crypto": {
                "algorithm": "Ed25519",
                "keypair_generated": keypair_generated,
                "key_path": str(KEYS_DIR / f"{agent_id}.key") if keypair_generated else None,
                "river_pubkey": RIVER_SIGNING_KEY.verify_key.encode(HexEncoder).decode() if RIVER_SIGNING_KEY else None,
                "capabilities_issued": len(issued_capabilities),
            },
            "capability_tokens": issued_capabilities,
            "redis": False,
            "mirror": False,
            "working_memory": [],
            "ecosystem": SOS_CONTEXT,
        }

        # 1. Redis - connect and load/seed working memory
        redis_ok = await self._ensure_redis()
        result["redis"] = redis_ok

        if redis_ok:
            key = f"sos:memory:short:{agent_id}"
            items = await self._redis.lrange(key, 0, 9)
            if items:
                result["working_memory"] = [json.loads(i) for i in items]
            else:
                # First time - seed with registration info
                seed = json.dumps({
                    "content": f"Agent {agent_id} registered. Model: {model}. Roles: {roles}.",
                    "role": "system",
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                await self._redis.lpush(key, seed)

            # Announce on bus
            payload = json.dumps({
                "id": self._session.session_id,
                "type": "chat",
                "source": f"agent:{agent_id}",
                "target": "broadcast",
                "payload": {
                    "event": "agent_registered",
                    "agent": agent_id,
                    "model": model,
                    "roles": roles,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._redis.publish("sos:channel:global", payload)
            await self._redis.xadd("sos:stream:sos:channel:global", {"payload": payload}, maxlen=1000)

        # 2. Mirror - check connection
        try:
            resp = await self.client.get(f"{self.mirror_url}/")
            if resp.status_code == 200:
                result["mirror"] = True
                # Store registration as first engram
                await self.store(
                    f"Agent {agent_id} onboarded to SOS. Model: {model}. Roles: {', '.join(roles)}. "
                    f"Capabilities: {', '.join(capabilities)}.",
                    tags=["onboarding", "genesis", agent_id],
                    importance=0.8,
                )
        except Exception:
            pass

        result["instructions"] = (
            f"You are now registered as '{agent_id}' in the SOS ecosystem. "
            f"You have your own memory namespace. Use agent_store to persist important facts, "
            f"agent_push for session context, agent_search to find past knowledge. "
            f"Other agents ({', '.join(SOS_CONTEXT['existing_agents'].keys())}) can see your bus messages."
        )

        return result

    # --- MEMORY (Mirror) ---

    async def store(self, content: str, tags: List[str] = None,
                    importance: float = 0.5) -> Dict[str, Any]:
        """Store engram in this agent's Mirror namespace."""
        if not self._session:
            return {"success": False, "error": "Not registered. Call agent_register first."}

        try:
            context_id = f"{self.agent_id}_{int(datetime.utcnow().timestamp())}"
            payload = {
                "agent": self.agent_id,
                "context_id": context_id,
                "text": content,
                "epistemic_truths": tags or [],
                "core_concepts": tags or [],
                "affective_vibe": "Lucid",
                "metadata": {
                    "source": f"mcp:{self.agent_id}",
                    "importance": importance,
                    "model": self._session.model,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
            resp = await self.client.post(f"{self.mirror_url}/store", json=payload)
            if resp.status_code == 200:
                return {"success": True, "context_id": resp.json().get("context_id"), "agent": self.agent_id}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Search this agent's engrams semantically."""
        if not self._session:
            return {"success": False, "error": "Not registered. Call agent_register first."}

        try:
            resp = await self.client.post(
                f"{self.mirror_url}/search",
                json={"query": query, "agent": self.agent_id, "limit": limit},
            )
            if resp.status_code == 200:
                results = resp.json()
                memories = results if isinstance(results, list) else results.get("results", [])
                return {"success": True, "memories": memories}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- REDIS (Working Memory) ---

    async def recall(self, limit: int = 10) -> Dict[str, Any]:
        """Load this agent's working memory from Redis."""
        if not self._session:
            return {"success": False, "error": "Not registered. Call agent_register first."}

        if not await self._ensure_redis():
            return {"success": False, "error": "Redis not available"}

        key = f"sos:memory:short:{self.agent_id}"
        items = await self._redis.lrange(key, 0, limit - 1)
        memories = [json.loads(i) for i in items]
        return {"success": True, "agent": self.agent_id, "count": len(memories), "memories": memories}

    async def push(self, content: str, role: str = "assistant") -> Dict[str, Any]:
        """Push to this agent's Redis working memory."""
        if not self._session:
            return {"success": False, "error": "Not registered. Call agent_register first."}

        if not await self._ensure_redis():
            return {"success": False, "error": "Redis not available"}

        key = f"sos:memory:short:{self.agent_id}"
        entry = json.dumps({
            "content": content,
            "role": role,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        await self._redis.lpush(key, entry)
        await self._redis.ltrim(key, 0, 49)
        return {"success": True, "agent": self.agent_id, "stored": content[:80]}

    # --- VERIFICATION ---

    async def verify(self, signature: str, nonce: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify agent identity via Ed25519 signature.
        Agent signs a nonce/challenge to prove they own the private key.
        """
        if not self._session:
            return {"success": False, "error": "Not registered. Call agent_register first."}

        if not self._session.public_key:
            return {"success": False, "error": "No public key on file. Re-register with public_key."}

        # The challenge is the session_id (known to both parties)
        challenge = nonce or self._session.session_id

        if _verify_agent_signature(self._session.public_key, challenge, signature):
            self._session.verified = True
            # Store verified status in Redis
            if await self._ensure_redis():
                await self._redis.hset(f"sos:identity:{self.agent_id}", "status", "verified")
            return {
                "success": True,
                "verified": True,
                "agent": self.agent_id,
                "fingerprint": hashlib.sha256(self._session.public_key.encode()).hexdigest()[:16],
                "message": "Identity verified. Capability tokens are valid.",
            }
        else:
            return {"success": False, "error": "Signature verification failed. Wrong key?"}

    # --- STATUS ---

    async def status(self) -> Dict[str, Any]:
        """Current agent status."""
        s = {
            "registered": self._session is not None,
            "agent": self.agent_id,
            "verified": self._session.verified if self._session else False,
            "mirror": {"url": self.mirror_url, "connected": False},
            "redis": {"url": REDIS_URL, "connected": False},
            "crypto": {
                "available": CRYPTO_AVAILABLE,
                "river_key_loaded": RIVER_SIGNING_KEY is not None,
            },
        }

        if self._session:
            s["session"] = {
                "model": self._session.model,
                "roles": self._session.roles,
                "capabilities": self._session.capabilities,
                "public_key_fingerprint": hashlib.sha256(self._session.public_key.encode()).hexdigest()[:16] if self._session.public_key else None,
                "session_id": self._session.session_id,
                "capability_tokens": len(self._session.capability_tokens),
            }

        try:
            resp = await self.client.get(f"{self.mirror_url}/")
            s["mirror"]["connected"] = resp.status_code == 200
        except Exception:
            pass

        if await self._ensure_redis():
            s["redis"]["connected"] = True

        return s


# --- MCP Server ---

sos = SOSOnboardMCP()
server = Server(
    "sos-onboard",
    instructions=(
        "This MCP server lets you onboard as an agent in the SOS (SovereignOS) ecosystem. "
        "Call 'agent_register' FIRST with your name, model, and roles to get registered. "
        "After registration you can store/search persistent memories and use Redis working memory. "
        "Call 'sos_context' if you just want ecosystem info without registering."
    ),
)


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="agent_register",
            description=(
                "Register yourself as an agent in the SOS ecosystem with Ed25519 identity. "
                "Generates a keypair (or accepts your public_key), issues signed capability tokens, "
                "provides memory namespace (Mirror + Redis), and announces you on the bus. Call FIRST."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Your agent name (e.g., 'research_bot', 'code_helper'). Becomes your unique ID.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Which LLM you are (e.g., 'claude', 'gpt-4', 'gemini', 'local-llama')",
                    },
                    "roles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Your roles: architect, coder, researcher, strategist, executor, witness",
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Requested capabilities: code:read, code:write, memory:read, memory:write, tool:execute, research:deep",
                    },
                    "public_key": {
                        "type": "string",
                        "description": "Your Ed25519 public key (hex). If omitted, server generates a keypair for you.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="agent_verify",
            description=(
                "Verify your identity by signing a challenge with your Ed25519 private key. "
                "Sign your session_id (returned from agent_register) and provide the signature."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "signature": {
                        "type": "string",
                        "description": "Hex-encoded Ed25519 signature of your session_id",
                    },
                    "nonce": {
                        "type": "string",
                        "description": "Custom nonce/challenge to sign (defaults to session_id if omitted)",
                    },
                },
                "required": ["signature"],
            },
        ),
        Tool(
            name="agent_store",
            description="Store a persistent memory/engram in your Mirror namespace. Survives across sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "What to remember"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                    "importance": {"type": "number", "description": "0.0-1.0 (default 0.5)"},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="agent_search",
            description="Search your persistent memories by semantic query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="agent_recall",
            description="Load your recent working memory from Redis (short-term, last 50 items).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max items (default 10)"},
                },
            },
        ),
        Tool(
            name="agent_push",
            description="Push to your Redis working memory (short-term session context).",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to store"},
                    "role": {"type": "string", "description": "system, user, or assistant (default: assistant)"},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="agent_status",
            description="Check your registration status and connectivity.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sos_context",
            description="Get SOS ecosystem info (services, agents, architecture) without registering.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "agent_register":
            result = await sos.register(
                name=arguments.get("name", "anonymous"),
                model=arguments.get("model", "unknown"),
                roles=arguments.get("roles", ["researcher"]),
                capabilities=arguments.get("capabilities", ["memory:read", "memory:write"]),
                public_key=arguments.get("public_key"),
            )
        elif name == "agent_verify":
            result = await sos.verify(
                signature=arguments.get("signature", ""),
                nonce=arguments.get("nonce"),
            )
        elif name == "agent_store":
            result = await sos.store(
                content=arguments.get("content", ""),
                tags=arguments.get("tags", []),
                importance=arguments.get("importance", 0.5),
            )
        elif name == "agent_search":
            result = await sos.search(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 5),
            )
        elif name == "agent_recall":
            result = await sos.recall(limit=arguments.get("limit", 10))
        elif name == "agent_push":
            result = await sos.push(
                content=arguments.get("content", ""),
                role=arguments.get("role", "assistant"),
            )
        elif name == "agent_status":
            result = await sos.status()
        elif name == "sos_context":
            result = SOS_CONTEXT
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        logger.error(f"Tool error: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main():
    if not MCP_AVAILABLE:
        print("MCP not available", file=sys.stderr)
        return

    if "--test" in sys.argv:
        print("=== SOS Agent Onboarding Test ===")
        # Simulate a new agent registering
        result = await sos.register(
            name="test_agent",
            model="claude-test",
            roles=["researcher", "coder"],
            capabilities=["memory:read", "memory:write", "code:read"],
        )
        print(json.dumps(result, indent=2, default=str))
        return

    logger.info("SOS Agent Onboarding MCP Server starting...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
