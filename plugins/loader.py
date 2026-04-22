"""Plugin loader — discovers and registers plugins with FastAPI."""
import logging
from typing import List

from fastapi import FastAPI

from .manifest import PluginManifest

logger = logging.getLogger("mirror.plugins")

_registry: List[PluginManifest] = []


def register(manifest: PluginManifest) -> None:
    """Register a plugin (idempotent — skips duplicates by name)."""
    if any(p.name == manifest.name for p in _registry):
        logger.debug(f"Plugin already registered: {manifest.name}")
        return
    _registry.append(manifest)
    logger.info(f"Plugin registered: {manifest.name} v{manifest.version}")


def mount_all(app: FastAPI) -> None:
    """Mount all registered plugin routers with FastAPI."""
    for manifest in _registry:
        if not manifest.enabled:
            continue
        router = manifest.get_router()
        if router:
            app.include_router(router)
            logger.info(f"Plugin mounted: {manifest.name}")


def summary() -> dict:
    return {
        "count": len([p for p in _registry if p.enabled]),
        "plugins": [
            {"name": p.name, "version": p.version, "enabled": p.enabled}
            for p in _registry
        ],
    }
