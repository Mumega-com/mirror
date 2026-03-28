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
import logging
from typing import List, Dict, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

try:
    from supabase import create_client, Client
    from openai import OpenAI
except ImportError:
    print("Error: Install dependencies: pip install supabase openai fastapi uvicorn")
    exit(1)

# Load environment
load_dotenv("/home/mumega/resident-cms/.env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mirror_api")

# Initialize clients
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

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
    """Generate embedding for text using OpenAI"""
    try:
        response = openai_client.embeddings.create(
            input=text,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding error: {e}")
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
task_init(supabase, openai_client)
app.include_router(task_router)

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
async def search_memory(request: SearchRequest) -> List[EngramResponse]:
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
async def store_engram(request: EngramStoreRequest):
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
async def get_recent_engrams(agent: str, limit: int = 10, project: Optional[str] = None):
    """
    Get recent engrams from a specific agent, optionally filtered by project

    Example: GET /recent/river?limit=5&project=gaf
    """
    try:
        query = supabase.table("mirror_engrams")\
            .select("*")\
            .ilike("series", f"%{agent}%")

        if project:
            query = query.eq("project", project)

        response = query\
            .order("timestamp", desc=True)\
            .limit(limit)\
            .execute()

        return {
            "agent": agent,
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
