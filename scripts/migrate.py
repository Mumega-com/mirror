#!/usr/bin/env python3
"""Mirror migration runner with schema_migrations tracking.

Usage:
    python scripts/migrate.py            # apply all pending migrations
    python scripts/migrate.py --status   # show applied/pending without running
    python scripts/migrate.py --dry-run  # print SQL that would run, no writes
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            k, _, v = _line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
);
"""


def _dsn() -> str:
    dsn = os.environ.get("MIRROR_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_CONNECTION_STRING", "")
    if not dsn:
        logger.error("No DATABASE_URL / MIRROR_DATABASE_URL / SUPABASE_CONNECTION_STRING set")
        sys.exit(1)
    return dsn


def _all_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations ORDER BY filename")
        return {row[0] for row in cur.fetchall()}


def status() -> None:
    conn = psycopg2.connect(_dsn())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(BOOTSTRAP)
        applied = _applied(conn)
        all_files = _all_files()
        print(f"\n{'File':<50} {'Status':>10}")
        print("-" * 62)
        for f in all_files:
            state = "applied" if f.name in applied else "PENDING"
            print(f"{f.name:<50} {state:>10}")
        pending = [f for f in all_files if f.name not in applied]
        print(f"\n{len(applied)} applied, {len(pending)} pending\n")
    finally:
        conn.close()


def run(dry_run: bool = False) -> None:
    conn = psycopg2.connect(_dsn())
    try:
        # Bootstrap tracking table
        with conn:
            with conn.cursor() as cur:
                cur.execute(BOOTSTRAP)

        applied = _applied(conn)
        pending = [f for f in _all_files() if f.name not in applied]

        if not pending:
            logger.info("All migrations already applied.")
            return

        logger.info("%d pending migration(s)%s", len(pending), " [DRY RUN]" if dry_run else "")

        for f in pending:
            sql = f.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]
            logger.info("%s  applying (%s)%s", f.name, checksum, " [DRY RUN]" if dry_run else "")

            if dry_run:
                print(f"\n-- {f.name} --\n{sql}\n")
                continue

            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                        (f.name, checksum),
                    )
            logger.info("%s  done", f.name)

        if not dry_run:
            logger.info("All pending migrations applied.")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror migration runner")
    parser.add_argument("--status", action="store_true", help="Show applied/pending without running")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL, no writes")
    args = parser.parse_args()

    if args.status:
        status()
    else:
        run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
