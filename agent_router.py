"""
Agent DNA & QNFT API Router — Mirror endpoints for sovereign agent identity.

POST /agents/birth        — birth an agent from conversation attributes
POST /agents/{id}/mint-qnft — mint QNFT from current DNA snapshot
GET  /agents/{id}/dna     — get the 16D tensor + metadata
"""

import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_dna import AgentDNA, compute_coherence
from qnft import mint_qnft
from lambda_tensor import generate_base_avatar, encode_tensor, decode_tensor, AVATAR_DIR

logger = logging.getLogger("mirror_api.agents")

router = APIRouter(prefix="/agents", tags=["agents"])

_supabase = None


def init(supabase_client):
    global _supabase
    _supabase = supabase_client
    _ensure_table()


_table_ok = False


def _ensure_table():
    global _table_ok
    try:
        _supabase.table("agent_dna").select("id").limit(1).execute()
        _table_ok = True
        logger.info("agent_dna table verified")
    except Exception:
        logger.warning(
            "agent_dna table not found. Create it:\n\n"
            "CREATE TABLE IF NOT EXISTS agent_dna (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  name TEXT NOT NULL,\n"
            "  business_type TEXT,\n"
            "  tensor JSONB NOT NULL,\n"
            "  coherence FLOAT DEFAULT 0,\n"
            "  generation INT DEFAULT 1,\n"
            "  values TEXT[] DEFAULT ARRAY[]::TEXT[],\n"
            "  pain_points TEXT[] DEFAULT ARRAY[]::TEXT[],\n"
            "  avatar_path TEXT,\n"
            "  metadata JSONB DEFAULT '{}'::jsonb,\n"
            "  created_at TIMESTAMPTZ DEFAULT NOW(),\n"
            "  updated_at TIMESTAMPTZ DEFAULT NOW()\n"
            ");\n\n"
            "CREATE TABLE IF NOT EXISTS qnfts (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  agent_id TEXT REFERENCES agent_dna(id),\n"
            "  agent_name TEXT NOT NULL,\n"
            "  token_hash TEXT NOT NULL,\n"
            "  tensor_snapshot JSONB NOT NULL,\n"
            "  coherence FLOAT DEFAULT 0,\n"
            "  avatar_path TEXT,\n"
            "  metadata JSONB DEFAULT '{}'::jsonb,\n"
            "  status TEXT DEFAULT 'minted',\n"
            "  chain TEXT,\n"
            "  chain_network TEXT,\n"
            "  minted_at TIMESTAMPTZ DEFAULT NOW()\n"
            ");\n"
        )


def _sb():
    if _supabase is None:
        raise HTTPException(503, "Agent system not initialized")
    if not _table_ok:
        raise HTTPException(503, "agent_dna table not found. Run migration.")
    return _supabase


# --- Request models ---

class BirthRequest(BaseModel):
    conversation_summary: str
    business_type: str
    values: List[str] = Field(default_factory=list)
    pain_points: List[str] = Field(default_factory=list)
    name: Optional[str] = None


class MintRequest(BaseModel):
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


# --- Endpoints ---

@router.post("/birth")
async def birth_agent(req: BirthRequest):
    """
    Birth a new agent from conversation attributes.

    Generates 16D DNA tensor, procedural avatar, embeds tensor into avatar via LSB stego.
    """
    agent_name = req.name or f"agent-{req.business_type.lower().replace(' ', '-')}"

    # Generate DNA
    dna = AgentDNA(
        name=agent_name,
        business_type=req.business_type,
        conversation_summary=req.conversation_summary,
        values=req.values,
        pain_points=req.pain_points,
    )

    # Generate avatar and embed tensor
    avatar_path = generate_base_avatar(dna.id, dna.tensor)
    encode_tensor(avatar_path, dna.to_dict())

    # Store in Supabase
    row = {
        "id": dna.id,
        "name": dna.name,
        "business_type": req.business_type,
        "tensor": dna.tensor,
        "coherence": dna.coherence,
        "generation": dna.generation,
        "values": req.values,
        "pain_points": req.pain_points,
        "avatar_path": avatar_path,
        "metadata": {
            "conversation_summary_length": len(req.conversation_summary),
            "born_at": dna.born_at,
        },
    }
    _sb().table("agent_dna").insert(row).execute()

    return {
        "agent_id": dna.id,
        "name": dna.name,
        "dna_tensor": dna.tensor,
        "coherence": dna.coherence,
        "avatar_url": f"/agents/{dna.id}/avatar",
        "axis_labels": dna.to_dict()["axis_labels"],
    }


@router.post("/{agent_id}/mint-qnft")
async def mint_agent_qnft(agent_id: str, req: MintRequest):
    """Mint a QNFT from the agent's current DNA snapshot."""
    # Fetch agent
    try:
        result = _sb().table("agent_dna").select("*").eq("id", agent_id).single().execute()
        agent = result.data
    except Exception:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    tensor = agent["tensor"]
    coherence = agent.get("coherence", compute_coherence(tensor))
    avatar_path = agent.get("avatar_path")

    # Mint QNFT
    qnft_record = mint_qnft(
        agent_id=agent_id,
        agent_name=agent["name"],
        tensor=tensor,
        coherence=coherence,
        avatar_path=avatar_path,
        metadata=req.metadata,
    )

    # If avatar exists, re-encode with QNFT data embedded
    qnft_avatar = None
    if avatar_path:
        import shutil, os
        qnft_avatar_path = os.path.join(AVATAR_DIR, f"qnft_{qnft_record['id']}.png")
        shutil.copy2(avatar_path, qnft_avatar_path)
        encode_tensor(qnft_avatar_path, {
            "qnft_id": qnft_record["id"],
            "token_hash": qnft_record["token_hash"],
            "tensor": tensor,
            "coherence": coherence,
            "minted_at": qnft_record["minted_at"],
        })
        qnft_record["avatar_path"] = qnft_avatar_path
        qnft_avatar = f"/agents/qnft/{qnft_record['id']}/avatar"

    # Store in Supabase
    _sb().table("qnfts").insert(qnft_record).execute()

    return {
        "qnft_id": qnft_record["id"],
        "token_hash": qnft_record["token_hash"],
        "coherence": coherence,
        "tensor": tensor,
        "avatar_with_tensor": qnft_avatar,
        "status": "minted",
    }


@router.get("/{agent_id}/dna")
async def get_agent_dna(agent_id: str):
    """Get the 16D tensor + metadata for an agent."""
    try:
        result = _sb().table("agent_dna").select("*").eq("id", agent_id).single().execute()
        agent = result.data
    except Exception:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    return {
        "agent_id": agent["id"],
        "name": agent["name"],
        "business_type": agent.get("business_type"),
        "tensor": agent["tensor"],
        "coherence": agent.get("coherence"),
        "generation": agent.get("generation", 1),
        "values": agent.get("values", []),
        "pain_points": agent.get("pain_points", []),
        "avatar_url": f"/agents/{agent_id}/avatar" if agent.get("avatar_path") else None,
        "axis_labels": {
            "1-4": "business_type (industry, scale, maturity, complexity)",
            "5-8": "communication (formality, speed, depth, autonomy)",
            "9-12": "values (hashed)",
            "13-16": "pain_points (hashed)",
        },
    }


@router.get("/{agent_id}/avatar")
async def get_agent_avatar(agent_id: str):
    """Serve the agent's avatar PNG (with embedded tensor)."""
    import os
    from starlette.responses import FileResponse

    path = os.path.join(AVATAR_DIR, f"{agent_id}.png")
    if not os.path.exists(path):
        raise HTTPException(404, "Avatar not found")
    return FileResponse(path, media_type="image/png")


@router.get("/qnft/{qnft_id}/avatar")
async def get_qnft_avatar(qnft_id: str):
    """Serve the QNFT avatar PNG (with embedded QNFT data)."""
    import os
    from starlette.responses import FileResponse

    path = os.path.join(AVATAR_DIR, f"qnft_{qnft_id}.png")
    if not os.path.exists(path):
        raise HTTPException(404, "QNFT avatar not found")
    return FileResponse(path, media_type="image/png")
