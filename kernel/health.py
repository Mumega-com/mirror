"""Health check contract."""
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class HealthStatus:
    status: str  # "healthy" | "degraded" | "unhealthy"
    service: str
    details: Dict[str, Any]


async def health_check(db) -> HealthStatus:
    try:
        db.count_engrams()
        return HealthStatus(status="healthy", service="mirror", details={"db": "ok"})
    except Exception as e:
        return HealthStatus(status="unhealthy", service="mirror", details={"db": str(e)})
