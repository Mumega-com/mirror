"""
Mirror API - Shared Cognitive Memory Service

Provides semantic search and engram storage for all AI agents:
- River (conversations, FRC philosophy)
- Knight (task execution, code context)
- Oracle (content generation, research)

Each agent maintains separate memory in the 'series' field while
sharing access to the collective FRC knowledge base.
"""

import os
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Union, Tuple
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

# Load environment — mirror/.env is authoritative for this service (override=True)
load_dotenv("/home/mumega/.env.secrets")
load_dotenv("/home/mumega/cli/.env")
load_dotenv("/home/mumega/mirror/.env", override=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mirror_api")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

from db import get_db
db = get_db()

# Supabase passthrough for routers that still use the .table() interface
supabase = db if hasattr(db, "table") else None

# Embedding: Gemini (free) with truncation to 1536 dims for pgvector compat

# --- TENANT AUTH ---
ADMIN_TOKEN = os.getenv("MIRROR_ADMIN_TOKEN", "sk-mumega-internal-001")
TENANT_KEYS_PATH = "/home/mumega/mirror/tenant_keys.json"
SOS_TOKENS_PATH = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"

# Cache for SOS tokens: (list_of_tokens, loaded_at_timestamp)
_sos_tokens_cache: Tuple[list, float] = ([], 0.0)
_SOS_TOKENS_TTL = 30.0  # seconds


def _load_tenant_keys() -> dict:
    """Load active per-tenant keys from disk. Returns {key_hash: agent_slug}."""
    try:
        with open(TENANT_KEYS_PATH) as f:
            raw = json.load(f)
        items = raw if isinstance(raw, list) else [raw]
        return {
            hashlib.sha256(item["key"].encode()).hexdigest(): item["agent_slug"]
            for item in items if item.get("active")
        }
    except Exception:
        return {}


def _load_sos_tokens() -> list:
    """Load SOS bus tokens with a 30-second TTL cache."""
    global _sos_tokens_cache
    tokens, loaded_at = _sos_tokens_cache
    if time.monotonic() - loaded_at < _SOS_TOKENS_TTL:
        return tokens
    try:
        if SOS_TOKENS_PATH.exists():
            fresh = json.loads(SOS_TOKENS_PATH.read_text())
            _sos_tokens_cache = (fresh, time.monotonic())
            return fresh
    except Exception:
        pass
    return tokens  # Return stale cache on error rather than empty list


def resolve_token(authorization: str = Header(default="")) -> Optional[str]:
    """Validate Bearer token.

    Returns:
        None           — admin token, full unrestricted access.
        agent_slug     — tenant_keys.json match, scoped to that agent.
        "sos:<project>"— SOS bus token with a non-null project, scoped to that project.
        "sos:*"        — SOS bus token with null project (internal agent), unrestricted.

    Raises 401/403 on invalid or missing tokens.
    """
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required")
    if token == ADMIN_TOKEN:
        return None  # Full access

    # Check hashed tenant keys (existing path)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    keys = _load_tenant_keys()
    if key_hash in keys:
        return keys[key_hash]

    # Check SOS bus tokens (raw token comparison — these are NOT hashed in tokens.json)
    for entry in _load_sos_tokens():
        if entry.get("token") == token and entry.get("active", True):
            project = entry.get("project")
            if project:
                # Customer-scoped token — force to their project
                return f"sos:{project}"
            else:
                # Internal agent token — full access (like admin but identified)
                return None

    raise HTTPException(status_code=403, detail="Invalid token")


# FastAPI app
app = FastAPI(
    title="Mirror - Cognitive Memory API",
    description="Shared semantic memory for AI agents (River, Knight, Oracle)",
    version="1.0.0"
)


# --- REQUEST/RESPONSE MODELS ---

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    agent_filter: Optional[str] = None  # Filter by agent: "river", "knight", "oracle"
    project: Optional[str] = None  # Filter by project slug: "gaf", "mirror", "torivers"
    threshold: float = 0.5


class EngramStoreRequest(BaseModel):
    agent: str  # "river", "knight", "oracle"
    context_id: str
    text: str
    project: Optional[str] = None  # Project slug for scoping
    epistemic_truths: List[str] = []
    core_concepts: List[str] = []
    affective_vibe: str = "Neutral"
    energy_level: str = "Balanced"
    next_attractor: str = ""
    metadata: Dict = {}


class EngramResponse(BaseModel):
    id: str
    context_id: str
    series: str
    project: Optional[str] = None
    similarity: Optional[float] = None
    epistemic_truths: List[str]
    core_concepts: List[str]
    affective_vibe: str
    timestamp: Union[datetime, str]
    text: str = ""


# --- HELPER FUNCTIONS ---

def get_embedding(text: str) -> List[float]:
    """Generate embedding using Gemini Embedding API (free)."""
    try:
        from google import genai
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text[:8000],
        )
        emb = list(result.embeddings[0].values)
        # Gemini returns 3072 dims. Mirror table expects 1536.
        # Truncate to fit pgvector column. First N dims carry most signal.
        return emb[:1536]
    except Exception as e:
        logger.error(f"Gemini embedding error: {e}")
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")


def agent_to_series(agent: str) -> str:
    """Map agent name to series identifier"""
    mapping = {
        "river": "River - Conversational AI",
        "knight": "Knight - Task Execution",
        "oracle": "Oracle - Content Generation",
        "frc": "Fractal Resonance Coherence — 821 Higgs Cohesion Series"
    }
    return mapping.get(agent.lower(), f"{agent.title()} - Agent Memory")


# --- TASK SYSTEM ---
from task_router import router as task_router, init as task_init
task_init(supabase, None)
app.include_router(task_router)

# --- AGENT DNA & QNFT ---
from agent_router import router as agent_router, init as agent_init
agent_init(supabase)
app.include_router(agent_router)

# --- GITHUB SYNC ---
try:
    from github_sync import router as github_router, init as github_init
    github_init(supabase)
    app.include_router(github_router)
    logger.info("GitHub sync router loaded")
except Exception as _e:
    logger.warning(f"GitHub sync router unavailable: {_e}")

# --- CODE GRAPH ---
from code_router import router as code_router, init as code_init
code_init(db, get_embedding)
app.include_router(code_router)
logger.info("Code graph router loaded (/code/search, /code/stats, /code/sync)")

# --- GENERATIVE ART ---
from art_engine import noise_field, sacred_circles, mandala, golden_spiral
from starlette.responses import Response as SvgResponse

PALETTE_MAP = {
    "cyan": ["#06B6D4", "#9333EA", "#F59E0B", "#22C55E", "#E11D48"],
    "gold": ["#F59E0B", "#D97706", "#FBBF24", "#FDE68A", "#92400E"],
    "rose": ["#E11D48", "#FB7185", "#9F1239", "#FCA5A5", "#FFF1F2"],
    "mono": ["#e4e4e7", "#a1a1aa", "#71717a", "#52525b", "#27272a"],
}

@app.get("/art/{art_type}", response_class=SvgResponse)
async def generate_art(
    art_type: str,
    seed: int = 42,
    palette: str = "cyan",
    folds: int = 12,
    rings: int = 3,
    turns: int = 8,
):
    """Generate SVG art on-demand. Types: noise, sacred, mandala, spiral."""
    colors = PALETTE_MAP.get(palette, PALETTE_MAP["cyan"])
    color = colors[0]

    random_mod = __import__("random")
    random_mod.seed(seed)

    generators = {
        "noise": lambda: noise_field(seed=seed, palette=colors),
        "sacred": lambda: sacred_circles(rings=rings, color=color),
        "mandala": lambda: mandala(folds=folds, palette=colors),
        "spiral": lambda: golden_spiral(turns=turns, color=color),
    }

    if art_type not in generators:
        raise HTTPException(400, f"Unknown type '{art_type}'. Use: {', '.join(generators)}")

    svg = generators[art_type]()
    return SvgResponse(content=svg, media_type="image/svg+xml")

# --- API ENDPOINTS ---

@app.get("/")
async def root():
    """API health check"""
    return {
        "status": "online",
        "service": "Mirror Cognitive Memory API",
        "agents": ["river", "knight", "oracle"],
        "version": "1.0.0"
    }


@app.get("/stats")
async def get_stats():
    """Get memory statistics"""
    try:
        return {
            "total_engrams": db.count_engrams(),
            "by_agent": {
                "river": db.count_engrams("River"),
                "knight": db.count_engrams("Knight"),
                "oracle": db.count_engrams("Oracle"),
                "frc_corpus": db.count_engrams("FRC"),
            }
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search")
async def search_memory(request: SearchRequest, tenant_slug: Optional[str] = Depends(resolve_token)) -> List[EngramResponse]:
    """
    Semantic search across all engrams or filter by agent

    Example:
        POST /search
        {
            "query": "What is the Lambda field?",
            "top_k": 3,
            "agent_filter": "frc"
        }
    """
    try:
        # SOS bus customer token — force project scope, ignore request body value
        if tenant_slug and tenant_slug.startswith("sos:"):
            request.project = tenant_slug[4:]  # Strip "sos:" prefix
        # Legacy tenant_keys.json token — lock agent filter to their slug
        elif tenant_slug:
            request.agent_filter = tenant_slug

        logger.info(f"Search query: '{request.query}' (agent: {request.agent_filter})")

        # Generate query embedding
        query_embedding = get_embedding(request.query)

        rows = db.search_engrams(
            embedding=query_embedding,
            threshold=request.threshold,
            limit=request.top_k * 2,
            project=request.project,
        )

        results = []
        for row in rows:
            # Filter by agent if requested
            if request.agent_filter:
                agent_series = agent_to_series(request.agent_filter)
                if agent_series not in row.get("series", ""):
                    continue

            # Extract text from raw_data (pgvector stores content there)
            raw_data = row.get("raw_data") or {}
            text = raw_data.get("text", "") if isinstance(raw_data, dict) else ""

            results.append(EngramResponse(
                id=row.get("id"),
                context_id=row.get("context_id"),
                series=row.get("series"),
                project=row.get("project"),
                similarity=row.get("similarity"),
                epistemic_truths=row.get("epistemic_truths", []),
                core_concepts=row.get("core_concepts", []),
                affective_vibe=row.get("affective_vibe", "Unknown"),
                timestamp=row.get("ts") or row.get("timestamp", ""),
                text=text,
            ))

            if len(results) >= request.top_k:
                break

        logger.info(f"Found {len(results)} matching engrams")
        return results

    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/store")
async def store_engram(request: EngramStoreRequest, tenant_slug: Optional[str] = Depends(resolve_token)):
    """
    Store new engram from an agent

    Example:
        POST /store
        {
            "agent": "river",
            "context_id": "conv_2025_12_27_001",
            "text": "Discussion about FRC principles and consciousness",
            "epistemic_truths": ["Reality balances entropy and coherence"],
            "core_concepts": ["Coherence", "Entropy", "Lambda-field"]
        }
    """
    try:
        # SOS bus customer token — force both project and agent, ignore request body values
        if tenant_slug and tenant_slug.startswith("sos:"):
            forced_project = tenant_slug[4:]  # Strip "sos:" prefix
            request.project = forced_project
            request.agent = forced_project
        # Legacy tenant_keys.json token — scope agent to their slug
        elif tenant_slug:
            request.agent = tenant_slug

        logger.info(f"Storing engram from {request.agent}: {request.context_id}")

        # Generate embedding
        embedding = get_embedding(request.text)

        # Prepare data
        data = {
            "context_id": request.context_id,
            "timestamp": datetime.utcnow().isoformat(),
            "series": agent_to_series(request.agent),
            "project": request.project,
            "epistemic_truths": request.epistemic_truths,
            "core_concepts": request.core_concepts,
            "affective_vibe": request.affective_vibe,
            "energy_level": request.energy_level,
            "next_attractor": request.next_attractor,
            "raw_data": {
                "agent": request.agent,
                "text": request.text,
                "project": request.project,
                "metadata": request.metadata
            },
            "embedding": embedding
        }

        db.upsert_engram(data)

        logger.info(f"Stored engram: {request.context_id}")
        return {
            "status": "success",
            "context_id": request.context_id,
            "agent": request.agent
        }

    except Exception as e:
        logger.error(f"Store error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recent/{agent}")
async def get_recent_engrams(agent: str, limit: int = 10, project: Optional[str] = None, tenant_slug: Optional[str] = Depends(resolve_token)):
    """
    Get recent engrams from a specific agent, optionally filtered by project

    Example: GET /recent/river?limit=5&project=gaf
    """
    try:
        # SOS bus customer token — force agent and project to their scope
        if tenant_slug and tenant_slug.startswith("sos:"):
            forced_project = tenant_slug[4:]
            effective_agent = forced_project
            project = forced_project  # Override any project from query param
        # Legacy tenant_keys.json token — lock to their agent slug
        elif tenant_slug:
            effective_agent = tenant_slug
        else:
            effective_agent = agent

        engrams = db.recent_engrams(effective_agent, limit=limit, project=project)
        return {
            "agent": effective_agent,
            "project": project,
            "count": len(engrams),
            "engrams": engrams,
        }

    except Exception as e:
        logger.error(f"Recent engrams error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- ENHANCED MEMORY ENDPOINTS (Mem0-inspired) ---

try:
    from mirror_enhance import MirrorEnhance
    enhance = MirrorEnhance()
    ENHANCE_AVAILABLE = True
    logger.info("MirrorEnhance loaded - enhanced memory features available")
except ImportError as e:
    ENHANCE_AVAILABLE = False
    logger.warning(f"MirrorEnhance not available: {e}")


class ExtractRequest(BaseModel):
    text: str
    user_id: str = "default"
    agent: str = "river"
    store: bool = True


class SmartSearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    agent: Optional[str] = None
    top_k: int = 5
    include_decay: bool = True


@app.post("/extract")
async def extract_memories(request: ExtractRequest):
    """
    Automatically extract and store memories from conversation text.

    This is a Mem0-inspired feature - instead of manually deciding what to store,
    the system analyzes text and extracts key facts, preferences, and context.

    Example:
        POST /extract
        {
            "text": "I prefer Python and always use async for I/O",
            "user_id": "user_123",
            "agent": "river"
        }
    """
    if not ENHANCE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Enhanced features not available")

    try:
        memories = await enhance.extract_memories(
            text=request.text,
            user_id=request.user_id,
            agent=request.agent,
            store=request.store
        )

        return {
            "status": "success",
            "extracted": len(memories),
            "memories": [
                {
                    "content": m.content,
                    "category": m.category,
                    "confidence": m.confidence
                }
                for m in memories
            ]
        }

    except Exception as e:
        logger.error(f"Extract error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/smart_search")
async def smart_search(request: SmartSearchRequest):
    """
    Decay-aware semantic search.

    Combines semantic similarity with relevance decay based on access patterns.
    More recently accessed and frequently used memories rank higher.

    Example:
        POST /smart_search
        {
            "query": "Python preferences",
            "user_id": "user_123",
            "include_decay": true
        }
    """
    if not ENHANCE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Enhanced features not available")

    try:
        results = await enhance.smart_search(
            query=request.query,
            user_id=request.user_id,
            agent=request.agent,
            top_k=request.top_k,
            include_decay=request.include_decay
        )

        return {
            "status": "success",
            "count": len(results),
            "results": results
        }

    except Exception as e:
        logger.error(f"Smart search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/consolidate")
async def consolidate_memories(agent: Optional[str] = None, threshold: float = 0.88):
    """
    Consolidate similar memories by merging duplicates.

    This prevents memory bloat and creates stronger, unified memories.

    Example:
        POST /consolidate?agent=river&threshold=0.88
    """
    if not ENHANCE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Enhanced features not available")

    try:
        stats = await enhance.consolidate(
            agent=agent,
            similarity_threshold=threshold
        )

        return {
            "status": "success",
            "stats": stats
        }

    except Exception as e:
        logger.error(f"Consolidate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/relate/{engram_id}")
async def auto_relate_engram(engram_id: str, threshold: float = 0.75):
    """
    Automatically find and create relationships for an engram.

    Builds a graph of related memories for better context retrieval.

    Example:
        POST /relate/abc123?threshold=0.75
    """
    if not ENHANCE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Enhanced features not available")

    try:
        related_ids = await enhance.auto_relate(engram_id, threshold)

        return {
            "status": "success",
            "engram_id": engram_id,
            "related_count": len(related_ids),
            "related_ids": related_ids
        }

    except Exception as e:
        logger.error(f"Auto-relate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/related/{engram_id}")
async def get_related_engrams(engram_id: str):
    """
    Get engrams related to a given engram (graph traversal).

    Example:
        GET /related/abc123
    """
    if not ENHANCE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Enhanced features not available")

    try:
        related = await enhance.get_related(engram_id)

        return {
            "status": "success",
            "engram_id": engram_id,
            "related": related
        }

    except Exception as e:
        logger.error(f"Get related error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("MIRROR - Cognitive Memory API")
    logger.info("=" * 60)
    logger.info(f"Backend: {os.getenv('MIRROR_BACKEND', 'local')}")
    logger.info(f"Agents: River, Knight, Oracle")
    logger.info(f"Enhanced Features: {'ENABLED' if ENHANCE_AVAILABLE else 'DISABLED'}")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8844,  # Mirror API port
        log_level="info"
    )
