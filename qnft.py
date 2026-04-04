"""
QNFT Minting — Quantum NFT snapshots of agent DNA state.

A QNFT captures the agent's 16D tensor at a moment in time.
Stored in Supabase (on-chain minting is a future step).
The avatar PNG carries the tensor via LSB steganography.
"""

import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("mirror.qnft")


def mint_qnft(
    agent_id: str,
    agent_name: str,
    tensor: list,
    coherence: float,
    avatar_path: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Mint a QNFT from the agent's current DNA state.

    Returns a QNFT record dict ready for Supabase insertion.
    """
    now = datetime.utcnow()
    ts = int(now.timestamp())

    # Token ID: agent_name + timestamp + short hash of tensor
    tensor_hash = hashlib.sha256(json.dumps(tensor).encode()).hexdigest()[:16]
    token_id = f"{agent_name}_{ts}_{tensor_hash[:8]}"

    # Full token hash for verification
    token_payload = json.dumps({
        "agent_id": agent_id,
        "tensor": tensor,
        "timestamp": ts,
    }, sort_keys=True)
    token_hash = hashlib.sha256(token_payload.encode()).hexdigest()

    qnft = {
        "id": token_id,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "token_hash": token_hash,
        "tensor_snapshot": tensor,
        "coherence": coherence,
        "avatar_path": avatar_path,
        "metadata": metadata or {},
        "status": "minted",
        "minted_at": now.isoformat(),
        "chain": None,  # on-chain tx hash (future)
        "chain_network": None,
    }

    logger.info(f"QNFT minted: {token_id} (coherence={coherence:.3f})")
    return qnft
