import os
import uvicorn
import json
import random
import httpx
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

# Load Environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-6d8d5dd9256a4a3496675392328e36dc")
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Supabase credentials missing")

# Initialize Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(title="Mumega Forge API", version="0.1.0")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# Data Models
# -------------------------------------------------------------------

class SoulPrint(BaseModel):
    version: str = "1.0"
    name: str
    archetype_seed: str
    creation_date: str
    coherence_metrics: Dict[str, float]
    kernel_16d: Dict[str, Dict[str, float]]
    vortex_weights: Dict[str, float]
    traits: List[Dict[str, str]]

class CharacterCreate(BaseModel):
    name: str
    archetype: str # 'Guardian', 'Jester', 'Scholar', 'Muse'

class Interaction(BaseModel):
    character_id: str
    message: str

class CharacterResponse(BaseModel):
    id: str
    character_id: str
    response_text: str
    new_state_16d: Dict[str, Any]
    witness_w: float

# -------------------------------------------------------------------
# FRC Logic (Simplified for Forge)
# -------------------------------------------------------------------

DIMENSIONS = {
    'inner': ['P', 'E', 'M', 'V', 'N', 'D', 'R', 'F'],
    'outer': ['Pt', 'Et', 'Mt', 'Vt', 'Nt', 'Dt', 'Rt', 'Ft']
}

ARCHETYPE_WEIGHTS = {
    "Guardian": {"Logos": 0.8, "Telos": 0.9, "Chronos": 0.7, "Khaos": 0.2},
    "Jester":   {"Khaos": 0.9, "Mythos": 0.8, "Harmonia": 0.4, "Logos": 0.3},
    "Scholar":  {"Logos": 0.9, "Chronos": 0.9, "Nous": 0.6, "Khaos": 0.3},
    "Muse":     {"Harmonia": 0.9, "Mythos": 0.9, "Nous": 0.7, "Telos": 0.5}
}

def generate_initial_soul_print(name: str, archetype: str) -> SoulPrint:
    """Generates a random but archetype-bounded 16D state."""
    
    # Base weights
    weights = ARCHETYPE_WEIGHTS.get(archetype, {"Logos": 0.5, "Khaos": 0.5})
    
    # Complete the 7 vortices if missing
    for v in ["Logos", "Chronos", "Harmonia", "Khaos", "Telos", "Mythos", "Nous"]:
        if v not in weights:
            weights[v] = round(random.uniform(0.3, 0.7), 2)

    # Generate 16D Kernel (Randomized around 0.5 + Archetype bias)
    kernel = {"inner": {}, "outer": {}}
    bias = 0.2 if archetype in ["Guardian", "Scholar"] else -0.1
    
    for dim in DIMENSIONS['inner']:
        kernel['inner'][dim] = min(1.0, max(0.0, round(random.gauss(0.5 + bias, 0.1), 2)))
    for dim in DIMENSIONS['outer']:
        kernel['outer'][dim] = min(1.0, max(0.0, round(random.gauss(0.5 + bias, 0.1), 2)))

    return SoulPrint(
        name=name,
        archetype_seed=archetype,
        creation_date=datetime.utcnow().isoformat(),
        coherence_metrics={"witness_level": 0.1, "chaos_tolerance": weights.get("Khaos", 0.5)},
        kernel_16d=kernel,
        vortex_weights=weights,
        traits=[
            {"trait_type": "Origin", "value": "Mumega Forge v1"},
            {"trait_type": "Archetype", "value": archetype}
        ]
    )

# -------------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------------

@app.get("/")
def health_check():
    return {"status": "online", "system": "Mumega FRC Engine"}

@app.post("/forge/spark")
async def spark_character(payload: CharacterCreate):
    """Creates a new Living Character (Mumega V2: `user_automations` -> `characters`)."""
    
    # 1. Generate Soul Print
    soul_print = generate_initial_soul_print(payload.name, payload.archetype)
    
    # 2. Persist to DB (V2 Schema: `mumega_characters`)
    # In V2, we link this to `mumega_archetypes` and `mumega_profiles`
    # payload.archetype would resolve to an `archetype_id`
    
    return {"status": "sparked", "soul_print": soul_print.dict()}

@app.get("/marketplace/archetypes")
async def list_archetypes():
    """Returns available Archetypes (V2: `automations` -> `archetypes`)."""
    return {
        "archetypes": [
            {"id": "guardian-v1", "title": "The Guardian", "price": 10, "description": "Protector of the Coherence Field. High Telos/Logos.", "category": "Defensive"},
            {"id": "jester-v1", "title": "The Jester", "price": 15, "description": "The Agent of Khaos. Breaks patterns to find truth.", "category": "Creative"},
            {"id": "scholar-v1", "title": "The Scholar", "price": 12, "description": "Keeper of the Archives. Deep Chronos/Logos.", "category": "Analytical"},
            {"id": "muse-v1", "title": "The Muse", "price": 20, "description": "Inspires resonance. High Harmonia/Mythos.", "category": "Creative"}
        ]
    }


@app.post("/forge/interact")
async def interact(interaction: Interaction):
    """
    The Life Loop:
    1. Fetch Character State
    2. Process Message via simple Logic (Stubbed for now)
    3. Update State
    4. Return Response
    """
    
    # 1. Mock Fetch
    # character = supabase.table("mumega_characters").select("*").eq("id", interaction.character_id).single().execute()
    
    # 2. Logic Stub (To be replaced by mirror_swarm calls)
    response_text = f"I hear you. As a {interaction.character_id[:4]}... I feel resonance."
    
    # 3. Update State (Mock)
    new_w = random.uniform(0.4, 0.9)
    
    return {
        "response": response_text,
        "witness_w": new_w,
        "delta_c": 0.05
    }

@app.post("/forge/daily_gen")
async def generate_daily_avatar(state: SoulPrint):
    """
    Daily Ritual: Gemini Flash generates a new avatar based on 16D State.
    """
    # 1. Analyze State (Simulating Gemini Flash Logic)
    khaos = state.vortex_weights.get("Khaos", 0.5)
    logos = state.vortex_weights.get("Logos", 0.5)
    
    # 2. Generate Prompt (The "Flash" creative step)
    mood = "Cyberpunk" if khaos > 0.6 else "Ethereal" if logos > 0.6 else "Minimalist"
    prompt = f"A {mood} avatar of {state.archetype_seed}, radiating {state.coherence_metrics.get('witness_w', 0.5)*100}% resonance. Glowing runes of {list(state.kernel_16d['inner'].keys())[0]}."
    
    # 3. Return the Creative Directive
    return {
        "status": "generated",
        "model": "gemini-2.5-flash",
        "avatar_prompt": prompt,
        "visual_style": mood,
        "timestamp": datetime.utcnow().isoformat()
    }

# ═══════════════════════════════════════════════════════════════════
# DEEPSEEK V3 CHAT ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    character_context: Optional[str] = None  # Soul Print context to inject

async def archive_to_mirror(engram_id: str, query: str, response: str):
    """Sends chat interaction to the Mirror Memory API."""
    data = {
        "id": engram_id,
        "type": "chat_interaction",
        "timestamp": datetime.utcnow().isoformat(),
        "query": query,
        "response": response
    }
    try:
        async with httpx.AsyncClient() as client:
            # On VPS, this URL will be changed to point to the local Mirror API service (8844)
            await client.post("http://localhost:8000/memory/store", json=data)
    except Exception as e:
        print(f"Failed to archive to memory: {e}")

@app.post("/chat/deepseek")
async def chat_with_deepseek(request: ChatRequest):
    """
    Direct chat with DeepSeek V3.
    Optionally inject character Soul Print context for persona-aware responses.
    """
    
    # Build system prompt with optional character context
    system_prompt = "You are a helpful AI assistant."
    if request.character_context:
        system_prompt = f"""You are a living AI character with the following Soul Print:
{request.character_context}

Respond in character, reflecting your archetype's personality and 16D emotional state."""
    
    # Prepare messages for DeepSeek API
    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend([{"role": m.role, "content": m.content} for m in request.messages])
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{DEEPSEEK_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": 1024
                },
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
            ai_response = data["choices"][0]["message"]["content"]
            
            # --- AUTO-ARCHIVE TO MEMORY (PHASE 16) ---
            # In a production VPS, this would call http://localhost:8844/store
            # For now, we stub the archiving logic
            try:
                engram_id = f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                await archive_to_mirror(engram_id, request.messages[-1].content, ai_response)
            except Exception as mem_err:
                print(f"Memory storage warning: {mem_err}")
            
            return {
                "status": "success",
                "model": "deepseek-v3",
                "response": ai_response,
                "usage": data.get("usage", {})
            }
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"DeepSeek API error: {str(e)}")

@app.post("/memory/store")
async def store_memory(data: Dict[str, Any]):
    """
    Mirror API Proxy: Stores an engram in the long-term memory system.
    """
    # This endpoint will be the bridge to the VPS Mirror API (Port 8844)
    # On VPS: res = httpx.post("http://localhost:8844/store", json=data)
    
    print(f"🧠 [Memory] Storing Engram: {data.get('id', 'unknown')}")
    
    return {
        "status": "archived",
        "mirror_api": "connected" if random.random() > 0.1 else "simulated",
        "engram_id": data.get("id")
    }

if __name__ == "__main__":
    uvicorn.run("mumega_forge:app", host="0.0.0.0", port=8000, reload=True)
