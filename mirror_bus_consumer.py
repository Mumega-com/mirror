"""
Mirror Bus Consumer — SOS streams → engrams

Subscribes to sos:stream:* via Redis XREAD and auto-stores an engram
for every v1 send/task_created/task_completed/announce message.

Checkpoint per stream in Redis (mirror:consumer:{stream}:last_id).
Idempotent: skips messages whose message_id already exists as an engram context_id.
Exceptions are logged; crashes are not propagated (Restart=always in systemd).

Environment (from /home/mumega/.env.secrets):
  REDIS_URL          — Redis connection URL (default: redis://localhost:6379)
  REDIS_PASSWORD     — Redis password (optional, can be baked into REDIS_URL)
  DATABASE_URL       — PostgreSQL connection string for Mirror DB
  MIRROR_BACKEND     — "local" (default) or "supabase"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import redis.asyncio as aioredis
from dotenv import load_dotenv

# Load env — mirror/.env overrides global secrets
load_dotenv("/home/mumega/.env.secrets")
load_dotenv("/home/mumega/mirror/.env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("mirror.bus_consumer")

# ---------------------------------------------------------------------------
# Mirror kernel — direct import (no HTTP, no shim)
# ---------------------------------------------------------------------------

sys.path.insert(0, "/home/mumega")
from mirror.kernel.db import get_db as _get_mirror_db          # noqa: E402
from mirror.kernel.embeddings import get_embedding as _get_mirror_embedding  # noqa: E402

_mirror_db = _get_mirror_db()   # singleton — initialized once, reused across messages

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")

# Stream patterns we want to tail
STREAM_SCAN_PATTERNS: list[str] = [
    "sos:stream:global:agent:*",
    "sos:stream:global:broadcast",
    "sos:stream:global:squad:*",
    # project-scoped patterns discovered dynamically
    "sos:stream:project:*:agent:*",
    "sos:stream:project:*:broadcast",
]

CHECKPOINT_KEY_PREFIX = "mirror:consumer"
BLOCK_MS = 2000          # XREAD blocking timeout
SCAN_INTERVAL_S = 30     # How often to re-scan for new streams
CONSUMER_NAME = "mirror-bus-consumer"

V1_TYPES_TO_STORE = {"send", "task_created", "task_completed", "announce"}

# ---------------------------------------------------------------------------
# DB / embedding helpers — delegate to Mirror kernel (imported above)
# ---------------------------------------------------------------------------


def _get_db():
    """Return the module-level Mirror DB singleton."""
    return _mirror_db


def _get_embedding(text: str) -> list[float]:
    """
    Generate a 1536-dim embedding via the Mirror kernel cascade:
    Gemini Embedding 2 → Gemini Embedding 1 → local ONNX → local hash.
    Never raises — last tier (hash) always succeeds.
    """
    try:
        return _get_mirror_embedding(text)
    except Exception as exc:
        log.warning("Embedding failed, using zero vector: %s", exc)
        return [0.0] * 1536


# ---------------------------------------------------------------------------
# Redis URL builder
# ---------------------------------------------------------------------------


def _build_redis_url() -> str:
    url = REDIS_URL
    if REDIS_PASSWORD and "@" not in url:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        netloc = f":{REDIS_PASSWORD}@{p.hostname}"
        if p.port:
            netloc += f":{p.port}"
        url = urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    return url


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


async def _load_checkpoint(r: aioredis.Redis, stream: str) -> str:
    """Return the last-seen message ID for this stream, or '0-0' if none."""
    key = f"{CHECKPOINT_KEY_PREFIX}:{stream}:last_id"
    val = await r.get(key)
    return val if val else "0-0"


async def _save_checkpoint(r: aioredis.Redis, stream: str, msg_id: str) -> None:
    key = f"{CHECKPOINT_KEY_PREFIX}:{stream}:last_id"
    await r.set(key, msg_id)


# ---------------------------------------------------------------------------
# Stream discovery
# ---------------------------------------------------------------------------


async def _discover_streams(r: aioredis.Redis, patterns: list[str]) -> set[str]:
    found: set[str] = set()
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match=pattern, count=200)
            for k in keys:
                found.add(k)
            if cursor == 0:
                break
    return found


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------


def _extract_engram_payload(
    stream: str,
    msg_id: str,
    fields: dict,
) -> Optional[dict]:
    """
    Parse a Redis stream message and return an engram dict or None if not applicable.
    """
    # Tolerate legacy (non-v1) messages
    msg_type = fields.get("type", "")
    if msg_type not in V1_TYPES_TO_STORE:
        return None

    # Parse payload JSON
    try:
        payload = json.loads(fields.get("payload", "{}"))
    except (json.JSONDecodeError, TypeError):
        payload = {}

    text: str = payload.get("text", "")
    if not text and msg_type in ("task_created", "task_completed"):
        # Fall back to title for task events
        text = payload.get("title", "")
    if not text:
        # Nothing worth storing
        return None

    # Source agent — strip "agent:" prefix
    source_raw: str = fields.get("source", "")
    agent = source_raw.removeprefix("agent:") if source_raw else "unknown"

    # Project — from the message itself or from stream path heuristic
    project: Optional[str] = fields.get("project") or payload.get("project")
    if not project:
        # Heuristic: sos:stream:project:<slug>:... → extract slug
        parts = stream.split(":")
        if "project" in parts:
            idx = parts.index("project")
            if idx + 1 < len(parts):
                project = parts[idx + 1]

    context_id = f"bus:{msg_id}"

    from datetime import datetime, timezone
    return {
        "context_id": context_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "series": f"{agent.title()} - Agent Memory",
        "project": project,
        "epistemic_truths": [],
        "core_concepts": [f"type:{msg_type}", f"stream:{stream}"],
        "affective_vibe": "Neutral",
        "energy_level": "Balanced",
        "next_attractor": "",
        "raw_data": {
            "agent": agent,
            "text": text,
            "project": project,
            "metadata": {
                "stream": stream,
                "msg_id": msg_id,
                "msg_type": msg_type,
                "source": source_raw,
            },
        },
    }


# ---------------------------------------------------------------------------
# Main consumer loop
# ---------------------------------------------------------------------------


async def consumer_loop() -> None:
    log.info("Mirror bus consumer starting — connecting to %s", REDIS_URL.split("@")[-1])

    redis_url = _build_redis_url()
    r = await aioredis.from_url(redis_url, decode_responses=True)

    db = _mirror_db  # already initialized at module level

    known_streams: set[str] = set()
    last_scan = 0.0

    while True:
        now = asyncio.get_event_loop().time()

        # Periodically rediscover streams
        if now - last_scan > SCAN_INTERVAL_S:
            discovered = await _discover_streams(r, STREAM_SCAN_PATTERNS)
            new = discovered - known_streams
            if new:
                log.info("Discovered %d new stream(s): %s", len(new), sorted(new)[:10])
            known_streams = discovered
            last_scan = now

        if not known_streams:
            await asyncio.sleep(5)
            continue

        # Build XREAD args: {stream: last_id, ...}
        stream_ids: dict[str, str] = {}
        for stream in known_streams:
            stream_ids[stream] = await _load_checkpoint(r, stream)

        # XREAD with block
        try:
            results = await r.xread(stream_ids, count=50, block=BLOCK_MS)
        except Exception as exc:
            log.error("XREAD error: %s", exc)
            await asyncio.sleep(5)
            continue

        if not results:
            continue

        for stream, messages in results:
            for msg_id, fields in messages:
                try:
                    engram = _extract_engram_payload(stream, msg_id, fields)
                    if engram is None:
                        await _save_checkpoint(r, stream, msg_id)
                        continue

                    # Idempotency check
                    existing = db.table("mirror_engrams").select("context_id").eq("context_id", engram["context_id"]).execute()
                    if existing.data:
                        log.debug("Skipping duplicate engram: %s", engram["context_id"])
                        await _save_checkpoint(r, stream, msg_id)
                        continue

                    # Generate embedding
                    text_for_embedding: str = engram["raw_data"]["text"]
                    engram["embedding"] = _get_embedding(text_for_embedding)

                    db.upsert_engram(engram)
                    log.info("Stored engram %s from %s", engram["context_id"], stream)

                except Exception as exc:
                    log.error(
                        "Failed to process msg %s on %s: %s",
                        msg_id, stream, exc, exc_info=True,
                    )

                # Always advance checkpoint, even on error, so we don't re-process indefinitely
                await _save_checkpoint(r, stream, msg_id)


if __name__ == "__main__":
    asyncio.run(consumer_loop())
