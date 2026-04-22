"""MCP server plugin manifest."""
from plugins.manifest import PluginManifest


def _make_router():
    from .routes import router
    return router


manifest = PluginManifest(
    name="mcp_server",
    version="1.0.0",
    description="MCP SSE server — Claude Desktop + ChatGPT connectivity",
    routes_factory=_make_router,
    mcp_tools=[],  # This plugin IS the MCP transport, not a tool consumer
)
