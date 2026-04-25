"""
Mirror Code Sync — push code-review-graph nodes into Mirror's Supabase pgvector.

Usage:
    python3 mirror_code_sync.py                  # sync all registered repos
    python3 mirror_code_sync.py --repo torivers  # sync one repo by name
    python3 mirror_code_sync.py --dry-run        # show what would be synced
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mirror_code_sync")

# Embedding via SOS kernel adapter (vertex → gemini → local cascade; no API key needed)
import sys as _sys
_SOS_PATH = str(Path.home() / "SOS")
if _SOS_PATH not in _sys.path:
    _sys.path.insert(0, _SOS_PATH)
from sos.kernel.embedding_adapter import embed as _kernel_embed

REGISTRY_PATH = Path.home() / ".code-review-graph" / "registry.json"

# Only embed these node kinds — skip noise
EMBED_KINDS = {
    "function", "method", "class", "module", "interface", "trait", "struct",
    "Function", "Method", "Class", "Module", "Interface", "Trait", "Struct",
}
BATCH_SIZE = 50  # upsert batch size
EMBED_DELAY = 0.1  # seconds between embedding calls (rate limit)


def get_embedding(text: str) -> list[float]:
    """Embed via SOS kernel cascade: vertex → gemini → local. No API key required."""
    return _kernel_embed(text[:8192])


def repo_short_name(repo_path: str) -> str:
    return Path(repo_path).name


def load_registry() -> list[str]:
    if not REGISTRY_PATH.exists():
        return []
    data = json.loads(REGISTRY_PATH.read_text())
    # Handle both {"repos": [...]} and flat list formats
    entries = data.get("repos", data) if isinstance(data, dict) else data
    return [entry["path"] if isinstance(entry, dict) else entry for entry in entries]


def graph_db_path(repo_path: str) -> Optional[Path]:
    db = Path(repo_path) / ".code-review-graph" / "graph.db"
    return db if db.exists() else None


def fetch_nodes(db_path: Path, kinds: set[str]) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"SELECT id, kind, name, qualified_name, file_path, line_start, line_end, language, signature "
        f"FROM nodes WHERE kind IN ({placeholders})",
        list(kinds),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_embed_text(node: dict) -> str:
    parts = []
    if node.get("signature"):
        parts.append(node["signature"])
    elif node.get("qualified_name"):
        parts.append(node["qualified_name"])
    else:
        parts.append(f"{node['kind']} {node['name']}")
    if node.get("file_path"):
        parts.append(f"in {node['file_path']}")
    return " ".join(parts)


def sync_repo(sb, repo_path: str, dry_run: bool = False) -> int:
    db = graph_db_path(repo_path)
    if not db:
        logger.warning(f"No graph.db for {repo_path} — skipping")
        return 0

    repo = repo_short_name(repo_path)
    nodes = fetch_nodes(db, EMBED_KINDS)
    logger.info(f"{repo}: {len(nodes)} nodes to sync")

    if dry_run:
        logger.info(f"[dry-run] would embed and upsert {len(nodes)} nodes for {repo}")
        return len(nodes)

    synced = 0
    batch: list[dict] = []

    for node in nodes:
        text = build_embed_text(node)
        try:
            embedding = get_embedding(text)
        except Exception as e:
            logger.error(f"Embed failed for {node['name']}: {e}")
            continue

        batch.append({
            "node_id": str(node["id"]),
            "repo": repo,
            "repo_path": repo_path,
            "kind": node["kind"],
            "name": node["name"],
            "qualified_name": node.get("qualified_name"),
            "file_path": node["file_path"],
            "line_start": node.get("line_start"),
            "line_end": node.get("line_end"),
            "language": node.get("language"),
            "signature": node.get("signature"),
            "embedding": embedding,
        })

        if len(batch) >= BATCH_SIZE:
            sb.upsert_code_nodes(batch)
            synced += len(batch)
            logger.info(f"  {repo}: upserted {synced}/{len(nodes)}")
            batch = []

        time.sleep(EMBED_DELAY)

    if batch:
        sb.upsert_code_nodes(batch)
        synced += len(batch)

    logger.info(f"{repo}: done — {synced} nodes synced")
    return synced


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync code graph nodes into Mirror pgvector")
    parser.add_argument("--repo", help="Repo short name to sync (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without writing")
    args = parser.parse_args()

    # No API key required — Vertex ADC or local fastembed via kernel cascade

    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from db import get_db
    sb = get_db()
    repos = load_registry()

    if not repos:
        logger.error("No repos registered. Run: code-review-graph register <path>")
        return

    if args.repo:
        repos = [r for r in repos if repo_short_name(r) == args.repo]
        if not repos:
            logger.error(f"Repo '{args.repo}' not found in registry")
            return

    total = 0
    for repo_path in repos:
        total += sync_repo(sb, repo_path, dry_run=args.dry_run)

    logger.info(f"Sync complete — {total} nodes total")


if __name__ == "__main__":
    main()
