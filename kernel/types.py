"""Shared Pydantic models for the Mirror kernel."""
from __future__ import annotations

from typing import Dict, List, Optional, Union
from datetime import datetime

from pydantic import BaseModel, Field, validator


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    agent_filter: Optional[str] = None  # Filter by agent: "river", "knight", "oracle"
    project: Optional[str] = None  # Filter by project slug: "gaf", "mirror", "torivers"
    threshold: float = 0.5

    @validator("query")
    def query_must_be_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must be nonempty")
        return stripped


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
    timestamp: Union[datetime, str]
    text: str = ""


class TokenContext(BaseModel):
    """Resolved auth token context passed to route handlers."""
    tenant_slug: Optional[str] = None  # None = admin/full access
