#!/usr/bin/env python3
"""
Re-embed mirror_engrams with gemini-embedding-2-preview.

Targets only rows where embedding_model IS NULL or != 'gemini-embedding-2-preview'.
Safe to re-run — already-migrated rows are skipped automatically.

Usage:
    python scripts/reembed_engrams.py             # live run
    python scripts/reembed_engrams.py --dry-run   # preview only, no writes
    python scripts/reembed_engrams.py --backup     # dump backup CSV before running
    python scripts/reembed_engrams.py --limit 100  # process only N rows (test)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

# Auto-load .env from mirror root so DATABASE_URL is available without `source .env`
from pathlib import Path as _Path
_env_file = _Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            k, _, v = _line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

# Add mirror root to path so we can import kernel
sys.path.insert(0, str(Path(__file__).parent.parent))
from kernel.embeddings import get_embedding

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reembed")

TARGET_MODEL = "gemini-embedding-2-preview"
BATCH_SIZE = 20          # rows per batch
SLEEP_BETWEEN_BATCHES = 1.0  # seconds — keeps us well under 1500 req/min
LOG_EVERY = 500          # rows


def get_conn() -> psycopg2.extensions.connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)


def fetch_candidates(conn, limit: int | None) -> list[dict]:
    """Fetch engrams that need re-embedding."""
    sql = """
        SELECT id, context_id, raw_data->>'text' AS text
        FROM mirror_engrams
        WHERE (embedding_model IS NULL OR embedding_model != %s)
          AND raw_data->>'text' IS NOT NULL
          AND raw_data->>'text' != ''
        ORDER BY timestamp DESC
    """
    params: list = [TARGET_MODEL]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def backup_csv(conn, path: str) -> None:
    """Dump current embeddings to CSV before modifying anything."""
    logger.info("Backing up current embeddings to %s ...", path)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, context_id, embedding_model,
                   LEFT(embedding::text, 80) AS embedding_preview
            FROM mirror_engrams
            WHERE embedding_model IS NULL OR embedding_model != %s
        """, [TARGET_MODEL])
        rows = cur.fetchall()

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "context_id", "embedding_model", "embedding_preview"])
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Backup written: %d rows → %s", len(rows), path)


def update_engram(conn, engram_id: str, embedding: list[float], dry_run: bool) -> None:
    if dry_run:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mirror_engrams
            SET embedding = %s::vector, embedding_model = %s
            WHERE id = %s
            """,
            [json.dumps(embedding), TARGET_MODEL, engram_id],
        )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed mirror_engrams with gemini-embedding-2-preview")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no writes")
    parser.add_argument("--backup", action="store_true", help="Dump CSV backup before running")
    parser.add_argument("--limit", type=int, default=None, help="Process only N rows")
    args = parser.parse_args()

    conn = get_conn()

    if args.backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = Path(__file__).parent.parent / f"backups/embeddings_backup_{ts}.csv"
        backup_path.parent.mkdir(exist_ok=True)
        backup_csv(conn, str(backup_path))

    candidates = fetch_candidates(conn, args.limit)
    total = len(candidates)

    if total == 0:
        logger.info("Nothing to re-embed — all engrams are already on %s", TARGET_MODEL)
        return

    logger.info(
        "%s %d engrams → %s%s",
        "DRY RUN:" if args.dry_run else "Re-embedding",
        total,
        TARGET_MODEL,
        " (no writes)" if args.dry_run else "",
    )

    success = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch = candidates[i : i + BATCH_SIZE]

        for row in batch:
            text = (row.get("text") or "").strip()
            if not text:
                logger.warning("Skipping %s — empty text", row["id"])
                failed += 1
                continue

            try:
                embedding = get_embedding(text)
                update_engram(conn, row["id"], embedding, args.dry_run)
                success += 1
            except Exception as e:
                logger.error("Failed %s: %s", row["id"], e)
                failed += 1

        processed = min(i + BATCH_SIZE, total)
        if processed % LOG_EVERY < BATCH_SIZE or processed == total:
            logger.info("Progress: %d/%d (✓ %d  ✗ %d)", processed, total, success, failed)

        # Rate limiting — stay well under 1500 req/min free tier
        if i + BATCH_SIZE < total:
            time.sleep(SLEEP_BETWEEN_BATCHES)

    logger.info(
        "Done. %s%d succeeded, %d failed, %d total.",
        "[DRY RUN] " if args.dry_run else "",
        success,
        failed,
        total,
    )

    conn.close()


if __name__ == "__main__":
    main()
