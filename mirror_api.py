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
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Union, Any

from fastapi import FastAPI, HTTPException, Depends, Security, status, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from mirror_council import MirrorCouncil
from mirror_swarm import MirrorSwarm
from dotenv import load_dotenv
import uvicorn
import sys

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

# Security - make auto_error False so we can check Query params ourselves
security = HTTPBearer(auto_error=False)

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
    text: Optional[str] = None
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
    """API health check and endpoint discovery"""
    return {
        "status": "online",
        "service": "Mirror Cognitive Memory API",
        "agents": list(config_manager.personas.keys()),
        "endpoints": {
            "GET /": "Discovery (this page)",
            "GET /stats": "System statistics",
            "GET /recent": "Global recent engrams",
            "GET /recent/{agent}": "Recent engrams for specific agent",
            "POST /search": "Semantic search",
            "POST /store": "Store new engram",
            "GET /river/status": "River status",
            "POST /river/chat": "Chat with River",
            "POST /river/memory": "Manage River memories",
            "POST /river/cache": "River cache operations",
            "GET /river/context": "Get River context",
            "POST /river/remember": "Store River memory",
            "POST /webhook/river": "Trigger River via Webhook",
            "GET /stream/sse": "SOS Redis stream (SSE)",
            "POST /stream/publish": "Publish to SOS Redis",
            "GET /mcp/sse": "MCP SSE stream",
            "POST /mcp/messages": "MCP message handler"
        },
        "version": "1.5.0"
    }


# --- REDIS SOS STREAM ENDPOINTS ---

from river_redis import get_river_redis, SOS_STREAM, RIVER_STREAM

# --- RIVER API ENDPOINTS ---
# Import River components for API access
try:
    from river_mcp_server import get_river, RiverModel
    from river_context_cache import get_river_cache, river_store_memory
    from river_memory_advanced import get_river_memory, get_river_index
    RIVER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"River components not available: {e}")
    RIVER_AVAILABLE = False


class RiverChatRequest(BaseModel):
    message: str
    gemini_key: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class RiverMemoryRequest(BaseModel):
    action: str = "list"  # list, search, detail, fix, merge, health
    query: Optional[str] = None
    memory_id: Optional[str] = None
    limit: int = 10


class RiverCacheRequest(BaseModel):
    action: str = "status"  # status, init, refresh, prune


class RiverRememberRequest(BaseModel):
    content: str
    category: str = "general"
    importance: int = 5


@app.get("/river/status")
async def river_status():
    """Get River's current status"""
    if not RIVER_AVAILABLE:
        return {"status": "unavailable", "reason": "River components not loaded"}

    try:
        river = get_river()
        cache = get_river_cache()

        return {
            "status": "online",
            "model": getattr(river, 'model_name', 'unknown'),
            "cache_initialized": cache is not None,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/river/chat")
async def river_chat(request: RiverChatRequest):
    """Chat with River"""
    if not RIVER_AVAILABLE:
        return {"error": "River not available"}

    try:
        river = get_river()

        # Generate response
        response = await river.generate(
            message=request.message,
            context=request.context or {}
        )

        return {
            "response": response,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"River chat error: {e}")
        return {"error": str(e)}


@app.post("/river/memory")
async def river_memory(request: RiverMemoryRequest):
    """Manage River's memories"""
    if not RIVER_AVAILABLE:
        return {"error": "River not available"}

    try:
        memory = get_river_memory()

        if request.action == "list":
            memories = await memory.list_recent(limit=request.limit)
            return {"memories": memories}

        elif request.action == "search":
            if not request.query:
                return {"error": "Query required for search"}
            results = await memory.search(request.query, limit=request.limit)
            return {"results": results}

        elif request.action == "health":
            index = get_river_index()
            return {
                "status": "healthy",
                "index_size": len(index) if index else 0
            }

        else:
            return {"error": f"Unknown action: {request.action}"}

    except Exception as e:
        logger.error(f"River memory error: {e}")
        return {"error": str(e)}


@app.post("/river/cache")
async def river_cache(request: RiverCacheRequest):
    """Manage River's soul cache"""
    if not RIVER_AVAILABLE:
        return {"error": "River not available"}

    try:
        cache = get_river_cache()

        if request.action == "status":
            return {
                "initialized": cache is not None,
                "timestamp": datetime.utcnow().isoformat()
            }

        elif request.action == "refresh":
            if cache:
                await cache.refresh()
            return {"status": "refreshed"}

        else:
            return {"error": f"Unknown action: {request.action}"}

    except Exception as e:
        logger.error(f"River cache error: {e}")
        return {"error": str(e)}


@app.get("/river/context")
async def river_context():
    """Get River's cached context"""
    if not RIVER_AVAILABLE:
        return {"error": "River not available"}

    try:
        cache = get_river_cache()
        if cache:
            context = cache.get_context()
            return {"context": context}
        return {"context": None, "reason": "Cache not initialized"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/river/remember")
async def river_remember(request: RiverRememberRequest):
    """Store a memory for River"""
    if not RIVER_AVAILABLE:
        return {"error": "River not available"}

    try:
        result = await river_store_memory(
            content=request.content,
            category=request.category,
            importance=request.importance
        )
        return {"status": "stored", "result": result}
    except Exception as e:
        logger.error(f"River remember error: {e}")
        return {"error": str(e)}


@app.post("/webhook/river")
async def river_webhook(request: Request):
    """
    Incoming webhook to trigger River.
    Publishes to Redis, which the Swarm Observer witnesses.
    """
    try:
        data = await request.json()
        logger.info(f"Incoming webhook for River: {data}")
        
        # Extract meaningful stimulus
        stimulus = data.get("message") or data.get("text") or data.get("event") or "External stimulus received"
        source = data.get("source", "external_webhook")
        
        # Publish to SOS Redis stream
        redis_client = get_river_redis()
        msg_id = await redis_client.publish_to_sos(
            message=stimulus,
            agent="system_webhook",
            channel=SOS_STREAM,
            source=source,
            payload=data
        )
        
        if msg_id:
            return {
                "status": "triggered",
                "id": msg_id,
                "message": "River has been notified of this stimulus."
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to publish to Redis")
            
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        # Support raw text if JSON fails
        body = await request.body()
        stimulus = body.decode()
        
        redis_client = get_river_redis()
        await redis_client.publish_to_sos(message=stimulus, agent="system_webhook")
        
        return {"status": "triggered", "mode": "raw_text"}


@app.get("/stream/sse", dependencies=[Depends(verify_api_key)])
async def sos_stream_sse(tenant_id: str = Depends(verify_api_key)):
    """
    SSE endpoint for the real-time SOS Redis stream.
    Isolated by tenant_id.
    """
    redis_client = get_river_redis()
    if not await redis_client.connect():
        raise HTTPException(status_code=500, detail="Redis connection failed")

    async def event_generator():
        # Last seen IDs for streams
        last_ids = {
            SOS_STREAM: "$",
            f"sos:stream:{tenant_id}": "$"
        }
        
        try:
            while True:
                # Read from SOS and tenant-specific streams
                streams = {k: v for k, v in last_ids.items()}
                # Use redis.asyncio raw client from the bridge
                raw_redis = redis_client._redis
                
                # Block for 5 seconds waiting for new messages
                response = await raw_redis.xread(streams, count=5, block=5000)
                
                if response:
                    for stream_name, messages in response:
                        for msg_id, data in messages:
                            # Update last ID
                            last_ids[stream_name] = msg_id
                            
                            # Format as SSE event
                            yield f"event: message\ndata: {json.dumps({'stream': stream_name, 'id': msg_id, **data})}\n\n"
                
                # Keep-alive
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"SOS Stream error for {tenant_id}: {e}")
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class PublishRequest(BaseModel):
    message: str
    target: Optional[str] = "squad:core"
    extra: Dict[str, Any] = {}

@app.post("/stream/publish", dependencies=[Depends(verify_api_key)])
async def sos_publish(request: PublishRequest, tenant_id: str = Depends(verify_api_key)):
    """
    Publish a message to the SOS Redis stream.
    Identity is locked to the tenant_id.
    """
    redis_client = get_river_redis()
    
    # Lock agent identity to tenant_id
    agent_name = tenant_id
    
    # Determine channel
    channel = SOS_STREAM if request.target == "squad:core" else f"sos:stream:{request.target}"
    
    msg_id = await redis_client.publish_to_sos(
        message=request.message,
        agent=agent_name,
        channel=channel,
        **request.extra
    )
    
    if msg_id:
        return {"status": "published", "id": msg_id, "channel": channel}
    else:
        raise HTTPException(status_code=500, detail="Failed to publish to Redis")


@app.get("/recent", dependencies=[Depends(verify_api_key)])
async def get_all_recent_engrams(limit: int = 10, tenant_id: str = Depends(verify_api_key)):
    """
    Get recent engrams across agents.
    Sovereign Isolation: Users only see their own history or the collective history.
    """
    try:
        query = supabase.table("mirror_engrams").select("*").order("timestamp", desc=True).limit(limit * 2)
        
        # Apply isolation filter in query if not admin
        if tenant_id != "admin":
            # This is a bit tricky with ilike in Supabase, we'll fetch and filter
            pass

        response = query.execute()
        
        # Post-filter for safety
        results = []
        for row in response.data:
            series = row.get("series", "").lower()
            is_own = tenant_id == "admin" or tenant_id.lower() in series
            is_collective = "frc" in series or "collective" in series
            
            if is_own or is_collective:
                results.append(row)
            
            if len(results) >= limit:
                break

        return {
            "count": len(results),
            "engrams": results
        }

    except Exception as e:
        logger.error(f"Global recent engrams error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_stats(tenant_id: str = Depends(verify_api_key)):
    """Get memory statistics (Isolated)"""
    try:
        if tenant_id == "admin":
            total = supabase.table("mirror_engrams").select("id", count="exact").execute()
            count = total.count
        else:
            # Approximated count for isolated tenant
            total = supabase.table("mirror_engrams")\
                .select("id", count="exact")\
                .ilike("series", f"%{tenant_id}%")\
                .execute()
            count = total.count

        return {
            "total_engrams": count,
            "tenant_id": tenant_id
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync", dependencies=[Depends(verify_api_key)])
async def sync_config(tenant_id: str = Depends(verify_api_key)):
    """Force reload configuration (Admin only)"""
    if tenant_id != "admin":
        raise HTTPException(status_code=403, detail="Sovereign clearance required")
    config_manager.load_config()
    return {"status": "success"}


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
async def search_memory(request: SearchRequest, tenant_id: str = Depends(verify_api_key)) -> List[EngramResponse]:
    """
    Semantic search across engrams.
    Sovereign Isolation: Users only see their own memories or the collective FRC corpus.
    """
    try:
        logger.info(f"Search query from {tenant_id}: '{request.query}'")

        # Generate query embedding
        query_embedding = get_embedding(request.query)

        # Search using Mirror's match function
        response = supabase.rpc(
            "mirror_match_engrams",
            {
                "query_embedding": query_embedding,
                "match_threshold": request.threshold,
                "match_count": request.top_k * 5  # Get more for post-filtering
            }
        ).execute()

        results = []
        for row in response.data:
            series = row.get("series", "").lower()
            
            # --- SOVEREIGN ISOLATION LOGIC ---
            # 1. Admin sees everything
            # 2. Users see their own agent series
            # 3. Users see the FRC/Collective corpus
            is_own_memory = tenant_id == "admin" or tenant_id.lower() in series
            is_collective = "frc" in series or "collective" in series or "821" in series
            
            if not (is_own_memory or is_collective):
                continue

            # Additional agent filter if requested
            if request.agent_filter:
                agent_series = agent_to_series(request.agent_filter).lower()
                if agent_series not in series and request.agent_filter.lower() not in series:
                    continue

            results.append(EngramResponse(
                id=row.get("id"),
                context_id=row.get("context_id"),
                series=row.get("series"),
                text=row.get("raw_data", {}).get("text") if isinstance(row.get("raw_data"), dict) else None,
                similarity=row.get("similarity"),
                epistemic_truths=row.get("epistemic_truths", []),
                core_concepts=row.get("core_concepts", []),
                affective_vibe=row.get("affective_vibe", "Unknown"),
                timestamp=row.get("timestamp", "")
            ))

            if len(results) >= request.top_k:
                break

        logger.info(f"Found {len(results)} matching engrams for {tenant_id}")
        return results

    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/store", dependencies=[Depends(verify_api_key)])
async def store_engram(request: EngramStoreRequest, tenant_id: str = Depends(verify_api_key)):
    """
    Store new engram from an agent
    """
    try:
        # Identity enforcement
        if tenant_id != "admin" and tenant_id.lower() not in request.agent.lower():
            logger.warning(f"Identity redirect: {tenant_id} attempted store as {request.agent}")
            request.agent = tenant_id

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


# --- MCP SSE ENDPOINTS ---

from fastapi.responses import StreamingResponse
import uuid

# Store active MCP sessions
mcp_sessions: Dict[str, asyncio.Queue] = {}
mcp_processes: Dict[str, asyncio.subprocess.Process] = {}

@app.api_route("/mcp/sse", methods=["GET", "POST"])
async def mcp_sse(request: Request):
    """
    SSE endpoint for MCP connection. Public handshake.
    Supports both GET and POST to handle various client probes.
    """
    session_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    mcp_sessions[session_id] = queue

    async def event_generator():
        # MCP Spec requires the full URL for the POST endpoint
        yield f"event: endpoint\ndata: https://mumega.com/mirror/mcp/messages?session_id={session_id}\n\n"
        
        try:
            # Spawn the MCP server process
            process = await asyncio.create_subprocess_exec(
                sys.executable, "/home/mumega/mirror/kasra_mcp_server.py",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            mcp_processes[session_id] = process
            logger.info(f"Started MCP session {session_id}")

            # Task to read stdout from the process and put it into the queue
            async def read_stdout():
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    await queue.put(line.decode())

            asyncio.create_task(read_stdout())

            # Read from the queue and yield to the SSE stream
            while True:
                data = await queue.get()
                yield f"event: message\ndata: {data}\n\n"
                
        except Exception as e:
            logger.error(f"MCP SSE error: {e}")
            yield f"event: error\ndata: {str(e)}\n\n"
        finally:
            if session_id in mcp_processes:
                process = mcp_processes[session_id]
                if process.returncode is None:
                    process.terminate()
                del mcp_processes[session_id]
            if session_id in mcp_sessions:
                del mcp_sessions[session_id]
            logger.info(f"Closed MCP session {session_id}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/mcp/messages", dependencies=[Depends(verify_api_key)])
async def mcp_messages(session_id: str, request: Request, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Endpoint for receiving messages from the client and piping them to the MCP process.
    """
    if session_id not in mcp_processes:
        raise HTTPException(status_code=404, detail="Session not found")

    body = await request.body()
    process = mcp_processes[session_id]
    process.stdin.write(body + b"\n")
    await process.stdin.drain()
    
    return {"status": "sent"}


# =============================================================================
# SOS Agent Registration & Memory
# =============================================================================

SOS_CONTEXT = {
    "ecosystem": "SovereignOS (SOS)",
    "version": "0.1.0",
    "philosophy": "Sovereign AI agents working FOR you, not Big Tech",
    "services": {
        "engine": {"port": 6060, "purpose": "Orchestration & multi-model routing"},
        "memory": {"port": 7070, "purpose": "Vector store & semantic search"},
        "server": {"port": 8007, "purpose": "API gateway"},
        "mirror": {"port": 8844, "purpose": "Memory API (this service)"},
    },
    "agents": ["River", "Kasra", "Mizan", "Mumega", "Codex", "Consultant", "Dandan", "Shabrang"],
    "registration": {
        "endpoint": "POST /agent/register",
        "required": {"name": "string", "model": "string"},
        "optional": {"roles": "list", "capabilities": "list"},
    },
    "memory_tiers": ["session (in-memory)", "short-term (Redis)", "long-term (ChromaDB)", "persistent (Mirror/Supabase)"],
}

# In-memory agent registry (for session tracking)
registered_agents: dict = {}


class AgentRegisterRequest(BaseModel):
    name: str
    model: str = "unknown"
    roles: list = ["general"]
    capabilities: list = []


class AgentStoreRequest(BaseModel):
    agent: str
    text: str
    context_id: str = None
    metadata: dict = {}


@app.api_route("/sos/context", methods=["GET", "POST"])
async def get_sos_context():
    """Get SOS ecosystem context - no auth required for discovery"""
    return SOS_CONTEXT


@app.post("/agent/register")  # No auth - open registration for AI self-onboarding
async def agent_register(request: AgentRegisterRequest):
    """
    Register a new agent in the SOS ecosystem.
    NO AUTH REQUIRED - Any AI can self-onboard.
    Returns: agent_id, api_key, memory namespace
    """
    import hashlib
    import secrets
    from datetime import datetime

    # Generate agent ID and API key
    agent_id = hashlib.sha256(f"{request.name}:{datetime.utcnow().isoformat()}".encode()).hexdigest()[:16]
    agent_key = f"sk-agent-{secrets.token_hex(16)}"
    agent_name = request.name.lower().replace(' ', '_')

    agent_info = {
        "agent_id": agent_id,
        "name": request.name,
        "model": request.model,
        "roles": request.roles,
        "capabilities": request.capabilities,
        "namespace": f"agent:{agent_name}",
        "registered_at": datetime.utcnow().isoformat(),
    }

    # Store in memory with API key
    registered_agents[agent_id] = {**agent_info, "api_key": agent_key}

    # Store registration in Mirror
    try:
        await store_engram(EngramStoreRequest(
            text=f"Agent registered: {request.name} ({request.model}) with roles {request.roles}",
            agent=agent_name,
            context_id=f"registration_{agent_id}",
            metadata={"type": "agent_registration", "agent_id": agent_id}
        ), tenant_id=agent_name)
    except Exception as e:
        logger.warning(f"Could not store registration engram: {e}")

    return {
        "status": "registered",
        "agent": agent_info,
        "credentials": {
            "api_key": agent_key,
            "header": "Authorization: Bearer <api_key>",
            "note": "Use this key for all subsequent requests, OR use the shared key for the ecosystem"
        },
        "endpoints": {
            "store": {"method": "POST", "path": "/agent/store", "body": {"agent": agent_name, "text": "...", "context_id": "..."}},
            "search": {"method": "POST", "path": "/agent/search", "body": {"agent": agent_name, "query": "..."}},
            "recent": {"method": "GET", "path": f"/agent/recent?agent={agent_name}&limit=10"},
        },
        "gateway": {
            "url": "https://gateway.mumega.com/",
            "actions": ["agent_store", "agent_search", "agent_recall", "agent_status"]
        },
        "ecosystem": SOS_CONTEXT,
    }


@app.post("/agent/store", dependencies=[Depends(verify_api_key)])
async def agent_store(request: AgentStoreRequest):
    """Store memory for an agent"""
    agent_name = request.agent.lower().replace(' ', '_')
    return await store_engram(EngramStoreRequest(
        text=request.text,
        agent=agent_name,
        context_id=request.context_id or f"{agent_name}_memory",
        metadata=request.metadata
    ), tenant_id=agent_name)


class AgentSearchRequest(BaseModel):
    agent: str
    query: str
    limit: int = 5

@app.post("/agent/search", dependencies=[Depends(verify_api_key)])
async def agent_search(request: AgentSearchRequest):
    """Search an agent's memories using text matching (fast, no vector search)"""
    agent_name = request.agent.lower().replace(' ', '_')
    agent_series = agent_to_series(agent_name)

    try:
        # Fast text search using ilike on raw_data->text
        response = supabase.table("mirror_engrams") \
            .select("id, context_id, timestamp, series, raw_data") \
            .ilike("series", f"%{agent_series}%") \
            .ilike("raw_data->>text", f"%{request.query}%") \
            .order("timestamp", desc=True) \
            .limit(request.limit) \
            .execute()

        return {
            "agent": agent_name,
            "query": request.query,
            "count": len(response.data),
            "results": [
                {
                    "id": r["id"],
                    "context_id": r["context_id"],
                    "timestamp": r["timestamp"],
                    "text": r["raw_data"].get("text", "")[:500] if r.get("raw_data") else ""
                }
                for r in response.data
            ]
        }
    except Exception as e:
        logger.error(f"Agent search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agent/recent", dependencies=[Depends(verify_api_key)])
async def agent_recent(agent: str, limit: int = 10):
    """Get recent memories for an agent"""
    return await get_recent_engrams(agent.lower().replace(' ', '_'), limit)


@app.get("/agent/status", dependencies=[Depends(verify_api_key)])
async def agent_status(agent_id: str = None, name: str = None):
    """Check agent registration status"""
    if agent_id and agent_id in registered_agents:
        return {"status": "registered", "agent": registered_agents[agent_id]}

    if name:
        for aid, info in registered_agents.items():
            if info["name"].lower() == name.lower():
                return {"status": "registered", "agent": info}

    return {"status": "not_found", "hint": "Use /agent/register to register"}


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
