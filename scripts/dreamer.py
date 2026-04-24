#!/usr/bin/env python3
"""
Mirror Dreamer — nightly memory consolidation.

Scores engrams, promotes tiers, archives low-value old memories.
Runs as a systemd oneshot at 03:30 UTC (after backup at 03:00).

Usage:
    python scripts/dreamer.py              # live run
    python scripts/dreamer.py --dry-run    # preview, no writes
    python scripts/dreamer.py --stats      # show tier distribution and exit
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Auto-load .env from mirror root
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            k, _, v = _line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

from kernel.db import get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dreamer")

# Tier promotion: current_tier → (target_tier, min_score, min_age_days)
_PROMOTE: dict[str, tuple[str, float, int]] = {
    "working":   ("episodic",   0.4,  0),
    "episodic":  ("long_term",  0.7,  7),
    "long_term": ("procedural", 0.8, 30),
}

_ARCHIVE_SCORE_THRESHOLD = 0.1
_ARCHIVE_AGE_DAYS = 365


def _days_old(engram: dict) -> float:
    ts_raw = engram.get("timestamp") or ""
    try:
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            ts = ts_raw
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except Exception:
        return 0.0


def score_engram(engram: dict) -> float:
    """Compute importance score.

    importance_score = (base_weight * recency_factor * reference_count) / 8.0
    """
    days = _days_old(engram)
    recency_factor = 1.0 / (1.0 + days * 0.1)
    raw_data = engram.get("raw_data") or {}
    if isinstance(raw_data, str):
        import json
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}
    base_weight = 5.0 if raw_data.get("pinned") else 1.0
    reference_count = max(1, engram.get("reference_count") or 1)
    return (base_weight * recency_factor * reference_count) / 8.0


def promote_tier(engram: dict, score: float) -> str:
    """Return the new tier for an engram, or its current tier if no promotion."""
    current = engram.get("memory_tier") or "working"
    if current not in _PROMOTE:
        return current
    target_tier, min_score, min_age_days = _PROMOTE[current]
    days = _days_old(engram)
    if score > min_score and days >= min_age_days:
        return target_tier
    return current


def should_archive(engram: dict, score: float) -> bool:
    """Return True if this engram should be archived."""
    if (engram.get("memory_tier") or "working") == "system":
        return False
    days = _days_old(engram)
    return score < _ARCHIVE_SCORE_THRESHOLD and days > _ARCHIVE_AGE_DAYS


def _notify(summary: dict) -> None:
    """Store Dreamer run summary as an engram and send to SOS bus."""
    promoted_total = sum(summary.get("promoted", {}).values())
    archived = summary.get("archived", 0)
    total = summary.get("total_processed", 0)
    text = (
        f"Dreamer completed: processed {total} engrams, "
        f"promoted {promoted_total}, archived {archived}. "
        f"Promotions: {summary.get('promoted', {})}."
    )

    try:
        db = get_db()
        db.upsert_engram({
            "context_id": f"dreamer-run-{summary['timestamp']}",
            "series": "Dreamer - Memory Consolidation",
            "project": "mirror",
            "memory_tier": "working",
            "importance_score": 0.5,
            "raw_data": {"text": text, "agent": "dreamer", "summary": summary},
            "epistemic_truths": [text],
            "core_concepts": ["memory_consolidation", "dreamer"],
            "affective_vibe": "reflective",
            "energy_level": 0.3,
            "next_attractor": "continue_consolidation",
        })
        logger.info("Summary stored as engram")
    except Exception as e:
        logger.warning("Failed to store summary engram: %s", e)

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "SOS"))
        from sovereign.kernel.bus import send as bus_send
        bus_send(to="athena", text=f"[Dreamer] {text}", from_agent="dreamer")
    except Exception:
        pass


def run(dry_run: bool = False) -> dict:
    """Execute one Dreamer consolidation cycle. Returns summary dict."""
    db = get_db()

    if not hasattr(db, "fetch_dreamable_engrams"):
        logger.error("Backend does not support fetch_dreamable_engrams — LocalDB only")
        return {"error": "unsupported backend"}

    engrams = db.fetch_dreamable_engrams()
    total = len(engrams)
    logger.info("%s%d engrams to process", "DRY RUN — " if dry_run else "", total)

    promoted: dict[str, int] = {}
    archived = 0
    scored = 0

    for engram in engrams:
        score = score_engram(engram)
        new_tier = promote_tier(engram, score)
        archive = should_archive(engram, score)

        old_tier = engram.get("memory_tier") or "working"
        tier_changed = new_tier != old_tier

        if not dry_run:
            db.update_engram_quality(
                engram_id=engram["id"],
                memory_tier=new_tier,
                importance_score=round(score, 4),
                archived=archive,
            )

        if archive:
            archived += 1
        elif tier_changed:
            key = f"{old_tier}→{new_tier}"
            promoted[key] = promoted.get(key, 0) + 1
        scored += 1

    summary = {
        "total_processed": scored,
        "promoted": promoted,
        "archived": archived,
        "dry_run": dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    promoted_total = sum(promoted.values())
    logger.info(
        "Done. Processed %d: promoted %d (%s), archived %d%s",
        scored,
        promoted_total,
        ", ".join(f"{k}={v}" for k, v in promoted.items()) or "none",
        archived,
        " [DRY RUN]" if dry_run else "",
    )

    if not dry_run:
        _notify(summary)
    return summary


def print_stats() -> None:
    """Print tier distribution for monitoring."""
    db = get_db()
    if not hasattr(db, "_conn"):
        print("Stats only available on LocalDB backend")
        return
    import psycopg2.extras
    with db._conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT memory_tier, COUNT(*) as count,
                       ROUND(AVG(importance_score)::numeric, 3) as avg_score,
                       COUNT(*) FILTER (WHERE archived) as archived_count
                FROM mirror_engrams
                GROUP BY memory_tier
                ORDER BY count DESC
            """)
            rows = cur.fetchall()
    print(f"\n{'Tier':<15} {'Count':>8} {'Avg Score':>10} {'Archived':>9}")
    print("-" * 46)
    for r in rows:
        print(f"{r['memory_tier'] or 'NULL':<15} {r['count']:>8} {float(r['avg_score']):>10.3f} {r['archived_count']:>9}")
    print()


def listen() -> None:
    """Subscribe to mirror.hot_store_threshold_exceeded and fire run() on each signal.

    Runs indefinitely as a persistent service (mirror-dreamer-listener.service).
    The nightly timer (mirror-dreamer.timer) continues to run independently.
    """
    import time as _time
    try:
        import redis as _redis
    except ImportError:
        logger.error("redis package not available — cannot start listener")
        sys.exit(1)

    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    channel = "mirror.hot_store_threshold_exceeded"

    r = _redis.Redis(host=redis_host, password=redis_password, decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe(channel)
    logger.info("Dreamer listener subscribed to '%s'", channel)

    for message in pubsub.listen():
        if message["type"] != "message":
            continue
        logger.info("Event-triggered Dreamer run (signal: %s)", message.get("data", "")[:120])
        try:
            summary = run()
            if "error" in summary:
                logger.error("Event-triggered Dreamer run failed: %s", summary["error"])
            else:
                logger.info(
                    "Event-triggered run complete: processed=%d promoted=%d archived=%d",
                    summary.get("total_processed", 0),
                    sum(summary.get("promoted", {}).values()),
                    summary.get("archived", 0),
                )
        except Exception as _e:
            logger.error("Event-triggered Dreamer run raised: %s", _e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror Dreamer — nightly memory consolidation")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no writes")
    parser.add_argument("--stats", action="store_true", help="Print tier distribution and exit")
    parser.add_argument("--listen", action="store_true",
                        help="Run as persistent listener, firing on hot-store threshold events")
    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    if args.listen:
        listen()
        return

    summary = run(dry_run=args.dry_run)
    if "error" in summary:
        sys.exit(1)


if __name__ == "__main__":
    main()
