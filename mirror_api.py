"""Mirror HTTP service.

This entrypoint is intentionally standalone: it mounts the generic Mirror
plugins and uses only the public kernel modules. SOS integration is opt-in and
loaded only when SOS_BUS_URL is configured.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from kernel.db import get_db
from kernel.health import health_check
from plugins import loader as plugin_loader
from plugins.admin.manifest import manifest as admin_manifest
from plugins.mcp_server.manifest import manifest as mcp_server_manifest
from plugins.memory.manifest import manifest as memory_manifest

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mirror.api")

app = FastAPI(
    title="Mirror",
    description="Self-hosted multi-tenant agent memory with Postgres RLS and MCP.",
    version="1.0.0",
)

plugin_loader.register(memory_manifest)
plugin_loader.register(mcp_server_manifest)
plugin_loader.register(admin_manifest)
plugin_loader.mount_all(app)

db = get_db()
_last_health_status: Optional[str] = None


def resolve_token(authorization: str = Header(default="")) -> Optional[str]:
    """Compatibility helper for older imports.

    Returns None for the root admin token, otherwise the resolved workspace id.
    """
    from kernel.auth import resolve_token_context

    ctx = resolve_token_context(authorization)
    if ctx.is_admin:
        return None
    return ctx.workspace_id


@app.get("/")
async def root() -> dict:
    return {
        "status": "online",
        "service": "mirror",
        "version": "1.0.0",
        "standalone": True,
        "plugins": plugin_loader.summary(),
    }


@app.get("/health")
async def health():
    """Return Mirror health with a bounded DB ping."""
    global _last_health_status

    status = await health_check(db)
    prev = _last_health_status
    _last_health_status = status.status

    if prev is not None and prev != status.status:
        logger.info(
            "health transition: %s -> %s (db_ms=%.1f)",
            prev,
            status.status,
            status.db_reachable_ms,
        )

    body = {
        "status": status.status,
        "service": status.service,
        "db_reachable": status.db_reachable,
        "db_reachable_ms": status.db_reachable_ms,
        "details": status.details,
    }
    if status.status == "healthy":
        return body
    return JSONResponse(status_code=503, content=body)


def _start_optional_sos_bus_subscriber() -> None:
    """Start optional SOS integration when an adapter is installed.

    The OSS tree does not ship an SOS subscriber. Deployments that use SOS can
    provide a module path via MIRROR_SOS_SUBSCRIBER_MODULE, and Mirror will call
    that module's start_thread(SOS_BUS_URL) or start_thread().
    """
    bus_url = os.getenv("SOS_BUS_URL")
    if not bus_url:
        return

    module_name = os.getenv("MIRROR_SOS_SUBSCRIBER_MODULE", "mirror_sos_subscriber")
    try:
        module = importlib.import_module(module_name)
        start_thread = getattr(module, "start_thread")
        try:
            start_thread(bus_url)
        except TypeError:
            start_thread()
        logger.info("SOS bus subscriber enabled via %s", module_name)
    except Exception as exc:
        logger.warning("SOS bus subscriber requested but unavailable: %s", exc)


if __name__ == "__main__":
    _start_optional_sos_bus_subscriber()
    port = int(os.getenv("MIRROR_PORT", "8844"))
    uvicorn.run(app, host=os.getenv("MIRROR_HOST", "0.0.0.0"), port=port)
