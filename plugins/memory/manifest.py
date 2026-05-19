"""Memory plugin manifest."""
from plugins.manifest import PluginManifest


def _make_router():
    from .routes import router
    return router


manifest = PluginManifest(
    name="memory",
    version="1.0.0",
    description="Engram store, search, and retrieval",
    routes_factory=_make_router,
    mcp_tools=[
        {"name": "memory_search", "description": "Semantic search across engrams"},
        {"name": "memory_store", "description": "Store new engram"},
        {"name": "memory_recent", "description": "List recent engrams"},
    ],
)
