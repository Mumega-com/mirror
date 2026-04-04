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
from typing import List, Dict, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: Install dependencies: pip install supabase fastapi uvicorn")
    exit(1)

# Load environment
load_dotenv("/home/mumega/.env.secrets")
load_dotenv("/home/mumega/cli/.env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mirror_api")

# Initialize clients
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Embedding: Gemini (free) with truncation to 1536 dims for pgvector compat
_gemini_configured = False
def _ensure_gemini():
    global _gemini_configured
    if not _gemini_configured:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_configured = True

# --- TENANT AUTH ---
ADMIN_TOKEN = os.getenv("MIRROR_ADMIN_TOKEN", "sk-mumega-internal-001")
TENANT_KEYS_PATH = "/home/mumega/mirror/tenant_keys.json"


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


def resolve_token(authorization: str = Header(default="")) -> Optional[str]:
    """Validate Bearer token. Returns None for admin, agent_slug for tenant, raises 401/403."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required")
    if token == ADMIN_TOKEN:
        return None  # Full access
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    keys = _load_tenant_keys()
    if key_hash not in keys:
        raise HTTPException(status_code=403, detail="Invalid token")
    return keys[key_hash]


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
    timestamp: str


# --- HELPER FUNCTIONS ---

def get_embedding(text: str) -> List[float]:
    """Generate embedding using local FastEmbed service (OpenAI-compatible)."""
    try:
        import httpx
        # Use our local sovereign embedding service on port 7997
        with httpx.Client() as client:
            resp = client.post(
                "http://localhost:7997/v1/embeddings",
                json={"input": text[:8000], "model": "local"},
                timeout=10.0
            )
            resp.raise_for_status()
            data = resp.json()
            emb = data["data"][0]["embedding"]
            
        # FastEmbed bge-small returns 384 dims. 
        # Mirror table expects 1536 (OpenAI/Gemini size).
        # We pad with zeros to maintain schema compatibility without migration.
        if len(emb) < 1536:
            emb.extend([0.0] * (1536 - len(emb)))
        return emb[:1536]
    except Exception as e:
        logger.error(f"Local embedding error: {e}")
        # Fallback to legacy _ensure_gemini logic could go here if needed
        raise HTTPException(status_code=500, detail=f"Sovereign embedding failed: {str(e)}")


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
        # Total engrams
        total = supabase.table("mirror_engrams").select("id", count="exact").execute()

        # Count by series (approximate agent distribution)
        river_count = supabase.table("mirror_engrams")\
            .select("id", count="exact")\
            .ilike("series", "%River%")\
            .execute()

        knight_count = supabase.table("mirror_engrams")\
            .select("id", count="exact")\
            .ilike("series", "%Knight%")\
            .execute()

        oracle_count = supabase.table("mirror_engrams")\
            .select("id", count="exact")\
            .ilike("series", "%Oracle%")\
            .execute()

        frc_count = supabase.table("mirror_engrams")\
            .select("id", count="exact")\
            .or_("series.ilike.%FRC%,series.ilike.%Fractal%,series.ilike.%821%")\
            .execute()

        return {
            "total_engrams": total.count,
            "by_agent": {
                "river": river_count.count,
                "knight": knight_count.count,
                "oracle": oracle_count.count,
                "frc_corpus": frc_count.count
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
        # Tenant token locks search to their agent namespace
        if tenant_slug:
            request.agent_filter = tenant_slug

        logger.info(f"Search query: '{request.query}' (agent: {request.agent_filter})")

        # Generate query embedding
        query_embedding = get_embedding(request.query)

        # Search using Mirror's match function (v2 supports project filtering)
        response = supabase.rpc(
            "mirror_match_engrams_v2",
            {
                "query_embedding": query_embedding,
                "match_threshold": request.threshold,
                "match_count": request.top_k * 2,  # Get more, filter later
                "filter_project": request.project
            }
        ).execute()

        results = []
        for row in response.data:
            # Filter by agent if requested
            if request.agent_filter:
                agent_series = agent_to_series(request.agent_filter)
                if agent_series not in row.get("series", ""):
                    continue

            results.append(EngramResponse(
                id=row.get("id"),
                context_id=row.get("context_id"),
                series=row.get("series"),
                project=row.get("project"),
                similarity=row.get("similarity"),
                epistemic_truths=row.get("epistemic_truths", []),
                core_concepts=row.get("core_concepts", []),
                affective_vibe=row.get("affective_vibe", "Unknown"),
                timestamp=row.get("timestamp", "")
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
        # Tenant token scopes the agent — can only store under their own slug
        if tenant_slug:
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

        # Upsert to Supabase
        result = supabase.table("mirror_engrams").upsert(
            data,
            on_conflict="context_id"
        ).execute()

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
        # Tenant token locks to their own agent slug
        effective_agent = tenant_slug if tenant_slug else agent

        query = supabase.table("mirror_engrams")\
            .select("*")\
            .ilike("series", f"%{effective_agent}%")

        if project:
            query = query.eq("project", project)

        response = query\
            .order("timestamp", desc=True)\
            .limit(limit)\
            .execute()

        return {
            "agent": effective_agent,
            "project": project,
            "count": len(response.data),
            "engrams": response.data
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
    logger.info(f"Supabase: {SUPABASE_URL}")
    logger.info(f"Agents: River, Knight, Oracle")
    logger.info(f"Enhanced Features: {'ENABLED' if ENHANCE_AVAILABLE else 'DISABLED'}")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8844,  # Mirror API port
        log_level="info"
    )
