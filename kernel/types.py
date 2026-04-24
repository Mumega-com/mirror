"""Shared Pydantic models for the Mirror kernel."""
from __future__ import annotations

from typing import Dict, List, Optional, Union
from datetime import datetime

from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    agent_filter: Optional[str] = None  # Filter by agent: "river", "knight", "oracle"
    project: Optional[str] = None  # Filter by project slug: "gaf", "mirror", "torivers"
    threshold: float = 0.5
    scope: Optional[str] = None  # "session" | "principal" | "workspace" | "org"


VALID_TIERS: frozenset[str] = frozenset({"public", "squad", "project", "entity", "private"})


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
    # Tier access model — optional; defaults to 'project' if not provided
    tier: Optional[str] = None
    entity_id: Optional[str] = None
    permitted_roles: Optional[List[str]] = None


class EngramResponse(BaseModel):
    id: str
    context_id: str
    series: str
    project: Optional[str] = None
    similarity: Optional[float] = None
    epistemic_truths: List[str]
    core_concepts: List[str]
    affective_vibe: str
    timestamp: Union[datetime, str]
    text: str = ""
    tier: str = "project"
    entity_id: Optional[str] = None


class TokenContext(BaseModel):
    """Resolved auth token context passed to route handlers."""
    tenant_slug: Optional[str] = None  # None = admin/full access
