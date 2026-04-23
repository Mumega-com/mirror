"""Admin plugin manifest — workspace and token management."""
from plugins.manifest import PluginManifest


def _make_router():
    from .routes import router
    return router


manifest = PluginManifest(
    name="admin",
    version="1.0.0",
    description="Workspace and token management for Mirror SaaS",
    routes_factory=_make_router,
)
