"""Plugin manifest contract — same pattern as SOS and Inkwell."""
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class PluginManifest:
    name: str
    version: str
    description: str
    routes_factory: Optional[Callable] = None  # returns APIRouter
    mcp_tools: List[dict] = field(default_factory=list)
    enabled: bool = True

    def get_router(self):
        if self.routes_factory:
            return self.routes_factory()
        return None
