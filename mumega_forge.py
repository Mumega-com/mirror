import os
import uvicorn
import json
import random
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
async def spark_character(payload: CharacterCreate): # Removed user_id dependency for MVP demo
    """Creates a new Living Character."""
    
    # 1. Generate Soul Print
    soul_print = generate_initial_soul_print(payload.name, payload.archetype)
    
    # 2. Persist to DB
    # Note: For MVP demo without active auth token, we might need a hardcoded user_id or handle anon
    # We will assume anon/public creation for this specific demo file, 
    # but in prod this uses request.user.
    
    # MOCK USER ID for demo purposes (The user HADI)
    # Ideally we'd fetch this from auth header
    # user_id = "00000000-0000-0000-0000-000000000000" 
    
    # For now, let's just return the object as if created, or try to insert if we had a user.
    # To make this real, let's just require a user_id in the payload for the DEV tool version.
    
    return {"status": "sparked", "soul_print": soul_print.dict()}


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

if __name__ == "__main__":
    uvicorn.run("mumega_forge:app", host="0.0.0.0", port=8000, reload=True)
