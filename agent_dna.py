"""
AgentDNA — 16D tensor generation from conversation attributes.

Maps real business/personality attributes to a 16D vector:
  axes  1-4:  business_type   (industry, scale, maturity, complexity)
  axes  5-8:  communication   (formality, speed, depth, autonomy)
  axes  9-12: values          (hashed from value strings)
  axes 13-16: pain_points     (hashed from pain point strings)

Each axis is normalized to [-1.0, 1.0].
"""

import hashlib
import math
import time
import uuid
from typing import List, Dict, Any, Optional


# --- Business type → axes 1-4 mappings ---

INDUSTRY_MAP = {
    "dental": [0.6, 0.3, 0.5, 0.2],
    "healthcare": [0.7, 0.4, 0.6, 0.5],
    "legal": [0.5, 0.6, 0.7, 0.6],
    "ecommerce": [0.4, 0.8, 0.3, 0.4],
    "saas": [0.3, 0.9, 0.4, 0.7],
    "agency": [0.5, 0.7, 0.5, 0.5],
    "restaurant": [0.6, 0.2, 0.3, 0.2],
    "realestate": [0.7, 0.5, 0.6, 0.4],
    "finance": [0.6, 0.8, 0.8, 0.8],
    "education": [0.4, 0.3, 0.7, 0.3],
    "consulting": [0.5, 0.6, 0.6, 0.5],
    "construction": [0.7, 0.3, 0.4, 0.3],
}

DEFAULT_INDUSTRY = [0.5, 0.5, 0.5, 0.5]


def _hash_to_axes(strings: List[str], n: int = 4) -> List[float]:
    """Deterministically map a list of strings to n axes in [-1, 1]."""
    if not strings:
        return [0.0] * n

    combined = "|".join(sorted(s.lower().strip() for s in strings))
    digest = hashlib.sha256(combined.encode()).hexdigest()

    axes = []
    for i in range(n):
        chunk = digest[i * 8 : (i + 1) * 8]
        val = int(chunk, 16) / 0xFFFFFFFF  # 0..1
        axes.append(val * 2 - 1)  # -1..1
    return axes


def _communication_axes(summary: str) -> List[float]:
    """Derive communication style axes from conversation summary text."""
    lower = summary.lower()
    length = len(summary)

    # Formality: presence of formal markers
    formal_markers = ["please", "would you", "kindly", "appreciate", "regarding"]
    informal_markers = ["hey", "gonna", "wanna", "lol", "btw", "asap"]
    formality = sum(1 for m in formal_markers if m in lower) - sum(1 for m in informal_markers if m in lower)
    formality = max(-1.0, min(1.0, formality / 3))

    # Speed: short summary = fast/terse communicator
    speed = max(-1.0, min(1.0, 1.0 - (length / 1000)))

    # Depth: presence of detail markers
    depth_markers = ["because", "specifically", "for example", "in detail", "the reason"]
    depth = sum(1 for m in depth_markers if m in lower)
    depth = max(-1.0, min(1.0, depth / 3))

    # Autonomy: preference for delegation vs control
    auto_markers = ["just do it", "handle it", "take care", "your call", "decide"]
    control_markers = ["check with me", "approval", "confirm first", "i want to see", "show me"]
    autonomy = sum(1 for m in auto_markers if m in lower) - sum(1 for m in control_markers if m in lower)
    autonomy = max(-1.0, min(1.0, autonomy / 2))

    return [formality, speed, depth, autonomy]


def generate_dna_tensor(
    business_type: str,
    conversation_summary: str = "",
    values: Optional[List[str]] = None,
    pain_points: Optional[List[str]] = None,
) -> List[float]:
    """
    Generate a 16D DNA tensor from real conversation attributes.

    Returns a list of 16 floats, each in [-1.0, 1.0].
    """
    # Axes 1-4: business type
    industry_key = business_type.lower().replace(" ", "").replace("-", "")
    biz_axes = INDUSTRY_MAP.get(industry_key, DEFAULT_INDUSTRY)
    # Normalize to [-1, 1]
    biz_axes = [v * 2 - 1 for v in biz_axes]

    # Axes 5-8: communication style
    comm_axes = _communication_axes(conversation_summary)

    # Axes 9-12: values
    val_axes = _hash_to_axes(values or [], 4)

    # Axes 13-16: pain points
    pain_axes = _hash_to_axes(pain_points or [], 4)

    return biz_axes + comm_axes + val_axes + pain_axes


def compute_coherence(tensor: List[float]) -> float:
    """ARF coherence score: how balanced/aligned the 16D vector is."""
    if not tensor:
        return 0.0
    magnitude = math.sqrt(sum(x * x for x in tensor))
    # Coherence = normalized magnitude vs theoretical max
    max_mag = math.sqrt(len(tensor))
    return min(1.0, magnitude / max_mag)


def compute_drift(old_tensor: List[float], new_tensor: List[float]) -> float:
    """Alpha drift: euclidean distance between two tensor states."""
    if len(old_tensor) != len(new_tensor):
        return 0.0
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(old_tensor, new_tensor)))


class AgentDNA:
    """Sovereign Genetic Code for an agent."""

    def __init__(
        self,
        name: str,
        business_type: str,
        conversation_summary: str = "",
        values: Optional[List[str]] = None,
        pain_points: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
    ):
        self.id = agent_id or str(uuid.uuid4())
        self.name = name
        self.business_type = business_type
        self.generation = 1
        self.born_at = time.time()

        self.tensor = generate_dna_tensor(
            business_type=business_type,
            conversation_summary=conversation_summary,
            values=values or [],
            pain_points=pain_points or [],
        )
        self.coherence = compute_coherence(self.tensor)

        self.values = values or []
        self.pain_points = pain_points or []
        self.conversation_summary = conversation_summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "business_type": self.business_type,
            "generation": self.generation,
            "born_at": self.born_at,
            "tensor": self.tensor,
            "coherence": self.coherence,
            "values": self.values,
            "pain_points": self.pain_points,
            "axis_labels": {
                "1-4": "business_type (industry, scale, maturity, complexity)",
                "5-8": "communication (formality, speed, depth, autonomy)",
                "9-12": "values (hashed)",
                "13-16": "pain_points (hashed)",
            },
        }

    def evolve(self, new_summary: str, new_values: Optional[List[str]] = None) -> float:
        """Evolve the DNA with new conversation data. Returns drift magnitude."""
        old_tensor = self.tensor[:]
        self.tensor = generate_dna_tensor(
            business_type=self.business_type,
            conversation_summary=new_summary,
            values=new_values or self.values,
            pain_points=self.pain_points,
        )
        self.coherence = compute_coherence(self.tensor)
        self.generation += 1
        return compute_drift(old_tensor, self.tensor)
