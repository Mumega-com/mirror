#!/usr/bin/env python3
"""Mirror migration runner with schema_migrations tracking.

Usage:
    python scripts/migrate.py --target mirror             # apply to Mirror localhost (DEFAULT)
    python scripts/migrate.py --target supabase           # apply to Supabase (app layer)
    python scripts/migrate.py --target mirror --status    # show applied/pending without running
    python scripts/migrate.py --target mirror --dry-run   # print SQL, no writes

IMPORTANT: --target is required when running against any live database.
The script prints the target host before connecting — verify it before proceeding.

Targets:
    mirror    postgresql://mirror:***@localhost:5432/mirror
    supabase  postgresql://postgres:***@db.nnolqgvuvoxkofbitunb.supabase.co:5432/postgres

If --target is omitted the script falls back to env-var lookup (legacy mode).
Env-var lookup is unsafe when .env files may have been rewritten — use --target.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
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

# ---------------------------------------------------------------------------
# Known targets — bypasses env-var lookup entirely, prevents wrong-DB errors.
# Credentials sourced from memory/reference_credentials.md (not from any .env).
# ---------------------------------------------------------------------------
_KNOWN_TARGETS: dict[str, tuple[str, str]] = {
    # target_name: (dsn, display_label)
    "mirror":   (
        "postgresql://mirror:mirror_local_2026@localhost:5432/mirror",
        "Mirror localhost:5432/mirror",
    ),
    "supabase": (
        "postgresql://postgres:UnnamedTao%408%40@db.nnolqgvuvoxkofbitunb.supabase.co:5432/postgres",
        "Supabase db.nnolqgvuvoxkofbitunb.supabase.co:5432/postgres",
    ),
}

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
);
"""


def _dsn(target: str | None) -> tuple[str, str]:
    """Return (dsn, display_label) for the given target.

    If *target* is a known name, bypass env-var lookup entirely (safe).
    If *target* is None, fall back to env-var lookup with a loud warning.
    """
    if target is not None:
        if target in _KNOWN_TARGETS:
            return _KNOWN_TARGETS[target]
        logger.error(
            "Unknown --target %r. Known targets: %s",
            target,
            ", ".join(_KNOWN_TARGETS),
        )
        sys.exit(1)

    # Legacy fallback — warn loudly that this is unsafe.
    logger.warning(
        "No --target specified. Reading DATABASE_URL from environment. "
        "This is UNSAFE if .env files have been rewritten by another agent. "
        "Pass --target mirror or --target supabase to be explicit."
    )
    dsn = (
        os.environ.get("MIRROR_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_CONNECTION_STRING", "")
    )
    if not dsn:
        logger.error("No DATABASE_URL / MIRROR_DATABASE_URL / SUPABASE_CONNECTION_STRING set")
        sys.exit(1)
    return dsn, "(from environment — unverified)"


def _all_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations ORDER BY filename")
        return {row[0] for row in cur.fetchall()}


def _connect_with_banner(target: str | None) -> psycopg2.extensions.connection:
    """Resolve DSN, print a safety banner, and open a connection."""
    dsn, label = _dsn(target)
    logger.info("TARGET: %s", label)
    return psycopg2.connect(dsn)


def status(target: str | None) -> None:
    conn = _connect_with_banner(target)
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


def run(target: str | None, dry_run: bool = False) -> None:
    conn = _connect_with_banner(target)
    try:
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
    parser.add_argument(
        "--target",
        choices=[*_KNOWN_TARGETS],
        default=None,
        help=(
            "Explicit database target — bypasses env-var lookup (recommended). "
            f"Choices: {', '.join(_KNOWN_TARGETS)}. "
            "Omit only if you are certain the environment is clean."
        ),
    )
    parser.add_argument("--status", action="store_true", help="Show applied/pending without running")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL, no writes")
    args = parser.parse_args()

    if args.status:
        status(args.target)
    else:
        run(args.target, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
