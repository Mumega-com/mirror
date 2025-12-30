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
    threshold: float = 0.5


class EngramStoreRequest(BaseModel):
    agent: str  # "river", "knight", "oracle"
    context_id: str
    text: str
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

        # Search using Mirror's match function
        response = supabase.rpc(
            "mirror_match_engrams",
            {
                "query_embedding": query_embedding,
                "match_threshold": request.threshold,
                "match_count": request.top_k * 2  # Get more, filter later
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
            "epistemic_truths": request.epistemic_truths,
            "core_concepts": request.core_concepts,
            "affective_vibe": request.affective_vibe,
            "energy_level": request.energy_level,
            "next_attractor": request.next_attractor,
            "raw_data": {
                "agent": request.agent,
                "text": request.text,
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
async def get_recent_engrams(agent: str, limit: int = 10):
    """
    Get recent engrams from a specific agent

    Example: GET /recent/river?limit=5
    """
    try:
        series_filter = agent_to_series(agent)

        response = supabase.table("mirror_engrams")\
            .select("*")\
            .ilike("series", f"%{agent}%")\
            .order("timestamp", desc=True)\
            .limit(limit)\
            .execute()

        return {
            "agent": agent,
            "count": len(response.data),
            "engrams": response.data
        }

    except Exception as e:
        logger.error(f"Recent engrams error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("MIRROR - Cognitive Memory API")
    logger.info("=" * 60)
    logger.info(f"Supabase: {SUPABASE_URL}")
    logger.info(f"Agents: River, Knight, Oracle")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8844,  # Mirror API port
        log_level="info"
    )
