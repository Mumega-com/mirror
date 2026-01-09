"""
Mirror API - Shared Cognitive Memory Service

Provides semantic search and engram storage for all AI agents:
- River (conversations, FRC philosophy)
- Knight (task execution, code context)
- Oracle (content generation, research)
- Dynamic Personas (from CLI configuration)

Each agent maintains separate memory in the 'series' field while
sharing access to the collective FRC knowledge base.
"""

import os
import asyncio
import json
import logging
import yaml
import glob
from typing import List, Dict, Optional, Union
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from mirror_council import MirrorCouncil
from mirror_swarm import MirrorSwarm
from dotenv import load_dotenv
import uvicorn

try:
    from supabase import create_client, Client
    from openai import OpenAI
except ImportError:
    print("Error: Install dependencies: pip install supabase openai fastapi uvicorn pyyaml")
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
# Master key for internal admin access (fallback)
MASTER_KEY = os.getenv("MUMEGA_MASTER_KEY", "sk-mumega-internal-001")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Constants
CLI_PATH = Path("/home/mumega/cli")
TENANTS_FILE = CLI_PATH / "config" / "tenants.json"
PERSONAS_DIR = CLI_PATH / "mumega" / "personas" / "local"

# Security
security = HTTPBearer()

# FastAPI app
app = FastAPI(
    title="Mirror - Cognitive Memory API",
    description="Shared semantic memory for AI agents (River, Knight, Oracle, and Squads)",
    version="1.1.0"
)


# --- DYNAMIC CONFIGURATION ---

class ConfigManager:
    def __init__(self):
        self.tenants: Dict[str, List[str]] = {}
        self.personas: Dict[str, Dict] = {}
        self.load_config()

    def load_config(self):
        """Load tenants and personas from CLI configuration"""
        self._load_tenants()
        self._load_personas()

    def _load_tenants(self):
        try:
            if TENANTS_FILE.exists():
                with open(TENANTS_FILE, "r") as f:
                    self.tenants = json.load(f)
                logger.info(f"Loaded {len(self.tenants)} tenants from {TENANTS_FILE}")
            else:
                logger.warning(f"Tenants file not found: {TENANTS_FILE}")
                self.tenants = {}
        except Exception as e:
            logger.error(f"Error loading tenants: {e}")
            self.tenants = {}

    def _load_personas(self):
        self.personas = {}
        try:
            if PERSONAS_DIR.exists():
                for file_path in PERSONAS_DIR.glob("*.yml"):
                    try:
                        with open(file_path, "r") as f:
                            data = yaml.safe_load(f)
                            # Create a simple mapping
                            name = data.get("name", file_path.stem)
                            self.personas[name.lower()] = {
                                "display_name": data.get("display_name", name.title()),
                                "description": data.get("description", ""),
                                "series_name": f"{data.get('display_name', name.title())} - Agent Memory"
                            }
                    except Exception as e:
                        logger.warning(f"Failed to load persona {file_path}: {e}")
                
                # Add default static personas if not present
                defaults = {
                    "river": "River - Conversational AI",
                    "knight": "Knight - Task Execution",
                    "oracle": "Oracle - Content Generation",
                    "frc": "Fractal Resonance Coherence — 821 Higgs Cohesion Series"
                }
                for key, val in defaults.items():
                    if key not in self.personas:
                        self.personas[key] = {"series_name": val}
                        
                logger.info(f"Loaded {len(self.personas)} personas")
            else:
                logger.warning(f"Personas directory not found: {PERSONAS_DIR}")
        except Exception as e:
            logger.error(f"Error loading personas: {e}")

config_manager = ConfigManager()


# --- AUTHENTICATION ---

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Verify Bearer token against tenants or master key"""
    token = credentials.credentials
    
    # Check master key
    if token == MASTER_KEY:
        return "admin"
        
    # Check tenant keys
    for tenant_id, keys in config_manager.tenants.items():
        if token in keys:
            return tenant_id
            
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired API Key",
        headers={"WWW-Authenticate": "Bearer"},
    )


# --- REQUEST/RESPONSE MODELS ---

class ConveneRequest(BaseModel):
    query: str
    force_all_agents: bool = False

class DispatchRequest(BaseModel):
    task: str
    foci: Optional[List[str]] = None

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    agent_filter: Optional[str] = None  # Filter by agent name
    threshold: float = 0.5


class EngramStoreRequest(BaseModel):
    agent: str 
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
    """Map agent name to series identifier dynamically"""
    agent_key = agent.lower()
    
    # Check loaded personas
    if agent_key in config_manager.personas:
        return config_manager.personas[agent_key]["series_name"]
        
    # Fallback
    return f"{agent.title()} - Agent Memory"


# --- API ENDPOINTS ---

@app.get("/")
async def root():
    """API health check"""
    return {
        "status": "online",
        "service": "Mirror Cognitive Memory API",
        "agents": list(config_manager.personas.keys()),
        "version": "1.1.0"
    }


@app.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_stats():
    """Get memory statistics"""
    try:
        # Total engrams
        total = supabase.table("mirror_engrams").select("id", count="exact").execute()

        # Count by series
        stats = {}
        for agent_key in config_manager.personas:
            series_name = config_manager.personas[agent_key]["series_name"]
            # Basic approximation for count query
            count = supabase.table("mirror_engrams")\
                .select("id", count="exact")\
                .ilike("series", f"%{agent_key}%")\
                .execute()
            stats[agent_key] = count.count

        frc_count = supabase.table("mirror_engrams")\
            .select("id", count="exact")\
            .or_("series.ilike.%FRC%,series.ilike.%Fractal%,series.ilike.%821%")\
            .execute()
        stats["frc_corpus"] = frc_count.count

        return {
            "total_engrams": total.count,
            "by_agent": stats
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync", dependencies=[Depends(verify_api_key)])
async def sync_config():
    """Force reload of tenants and personas configuration"""
    config_manager.load_config()
    return {
        "status": "success", 
        "tenants_count": len(config_manager.tenants),
        "personas_count": len(config_manager.personas),
        "personas": list(config_manager.personas.keys())
    }


@app.post("/council/convene", dependencies=[Depends(verify_api_key)])
async def convene_council(request: ConveneRequest):
    try:
        council = MirrorCouncil()
        loop = asyncio.get_running_loop()
        winner = await loop.run_in_executor(None, council.convene, request.query, request.force_all_agents)
        return winner
    except Exception as e:
        logger.error(f"Council error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/swarm/dispatch", dependencies=[Depends(verify_api_key)])
async def dispatch_swarm(request: DispatchRequest):
    try:
        swarm = MirrorSwarm()
        result = await swarm.coordinate(request.task, request.foci)
        return {"result": result}
    except Exception as e:
        logger.error(f"Swarm error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/search", dependencies=[Depends(verify_api_key)])
async def search_memory(request: SearchRequest) -> List[EngramResponse]:
    """
    Semantic search across all engrams or filter by agent
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
                # Fuzzy match because series names might change slightly or have suffixes
                if agent_series not in row.get("series", "") and request.agent_filter.lower() not in row.get("series", "").lower():
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


@app.post("/store", dependencies=[Depends(verify_api_key)])
async def store_engram(request: EngramStoreRequest):
    """
    Store new engram from an agent
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

@app.get("/engram/{engram_id}", dependencies=[Depends(verify_api_key)])
async def get_engram(engram_id: str):
    """Retrieve a single engram by ID"""
    try:
        response = supabase.table("mirror_engrams").select("*").eq("id", engram_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Engram not found")
        return response.data[0]
    except Exception as e:
        logger.error(f"Failed to fetch engram {engram_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recent/{agent}", dependencies=[Depends(verify_api_key)])
async def get_recent_engrams(agent: str, limit: int = 10):
    """
    Get recent engrams from a specific agent
    """
    try:
        # Use loose matching for agent series
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


@app.post("/extract", dependencies=[Depends(verify_api_key)])
async def extract_memories(request: ExtractRequest):
    """
    Automatically extract and store memories from conversation text.
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


@app.post("/smart_search", dependencies=[Depends(verify_api_key)])
async def smart_search(request: SmartSearchRequest):
    """
    Decay-aware semantic search.
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


@app.post("/consolidate", dependencies=[Depends(verify_api_key)])
async def consolidate_memories(agent: Optional[str] = None, threshold: float = 0.88):
    """
    Consolidate similar memories by merging duplicates.
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


@app.post("/relate/{engram_id}", dependencies=[Depends(verify_api_key)])
async def auto_relate_engram(engram_id: str, threshold: float = 0.75):
    """
    Automatically find and create relationships for an engram.
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


@app.get("/related/{engram_id}", dependencies=[Depends(verify_api_key)])
async def get_related_engrams(engram_id: str):
    """
    Get engrams related to a given engram (graph traversal).
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
    logger.info(f"Agents: {list(config_manager.personas.keys())}")
    logger.info(f"Enhanced Features: {'ENABLED' if ENHANCE_AVAILABLE else 'DISABLED'}")
    logger.info(f"Tenants Loaded: {len(config_manager.tenants)}")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8844,  # Mirror API port
        log_level="info"
    )
