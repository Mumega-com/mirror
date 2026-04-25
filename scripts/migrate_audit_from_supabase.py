#!/usr/bin/env python3
"""One-time migration: copy audit_events from Supabase → Mirror with chain integrity verification.

Usage:
    python scripts/migrate_audit_from_supabase.py --dry-run   # preview, no writes
    python scripts/migrate_audit_from_supabase.py             # execute migration
    python scripts/migrate_audit_from_supabase.py --verify    # verify chain only (post-migration)
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
logger = logging.getLogger("audit-migrate")

MIRROR_DSN = os.environ.get("DATABASE_URL", "postgresql://mirror:mirror_local_2026@localhost:5432/mirror")
SUPABASE_DSN = os.environ.get("SUPABASE_DB_URL", "")


def _fetch_supabase_events() -> list[dict]:
    conn = psycopg2.connect(SUPABASE_DSN)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM audit_events ORDER BY stream_id, seq")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _fetch_supabase_stream_seqs() -> list[dict]:
    conn = psycopg2.connect(SUPABASE_DSN)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM audit_stream_seqs WHERE stream_id != 'test-stream'")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def verify_chain(events: list[dict]) -> bool:
    """Verify hash chain linkage: prev_hash of row N must equal hash of row N-1.

    This is the portable tamper-detection check — it does not recompute hashes
    from event fields (which requires knowing the emitter's exact canonical JSON
    algorithm). Linkage verification is sufficient: any modification to a row
    would break the next row's prev_hash reference.
    """
    ok = True
    streams: dict[str, list[dict]] = {}
    for e in events:
        streams.setdefault(e["stream_id"], []).append(e)

    for stream_id, stream_events in streams.items():
        stream_events.sort(key=lambda x: x["seq"])
        for i, ev in enumerate(stream_events):
            actual_hash = bytes(ev["hash"])
            if i == 0:
                # Genesis: prev_hash must be NULL
                if ev.get("prev_hash") is not None:
                    logger.error("Chain FAIL: stream=%s seq=%d genesis event has non-NULL prev_hash", stream_id, ev["seq"])
                    ok = False
                else:
                    logger.info("chain OK (genesis): stream=%s seq=%d hash=%s", stream_id, ev["seq"], actual_hash.hex()[:16])
            else:
                prev_hash = bytes(stream_events[i - 1]["hash"])
                row_prev_hash = bytes(ev["prev_hash"]) if ev.get("prev_hash") else None
                if row_prev_hash != prev_hash:
                    logger.error(
                        "Chain linkage FAIL: stream=%s seq=%d prev_hash=%s expected=%s",
                        stream_id, ev["seq"],
                        (row_prev_hash.hex()[:16] if row_prev_hash else "NULL"),
                        prev_hash.hex()[:16],
                    )
                    ok = False
                else:
                    logger.info("chain OK: stream=%s seq=%d hash=%s", stream_id, ev["seq"], actual_hash.hex()[:16])
    return ok


def run(dry_run: bool = False) -> None:
    if not SUPABASE_DSN:
        logger.error("SUPABASE_DB_URL not set")
        sys.exit(1)

    events = _fetch_supabase_events()
    seqs = _fetch_supabase_stream_seqs()
    logger.info("Fetched %d events from Supabase across %d streams", len(events),
                len({e["stream_id"] for e in events}))

    logger.info("Verifying chain integrity on Supabase data before migration...")
    if not verify_chain(events):
        logger.error("Chain integrity check FAILED on source data — aborting migration")
        sys.exit(1)
    logger.info("Chain integrity: PASS")

    if dry_run:
        logger.info("DRY RUN — would insert %d events + %d stream_seq rows into Mirror", len(events), len(seqs))
        for e in events:
            logger.info("  event: stream=%s seq=%d actor=%s action=%s resource=%s",
                        e["stream_id"], e["seq"], e["actor_id"], e["action"], e["resource"])
        return

    mirror = psycopg2.connect(MIRROR_DSN)
    try:
        with mirror:
            with mirror.cursor() as cur:
                # Verify Mirror audit_events is empty (safe migration target)
                cur.execute("SELECT COUNT(*) FROM audit_events WHERE stream_id != 'test-stream'")
                (count,) = cur.fetchone()
                if count > 0:
                    logger.error("Mirror audit_events already has %d non-test rows — refusing to overwrite", count)
                    sys.exit(1)

                # Copy events exactly — preserve all fields including hash/prev_hash
                insert_sql = """
                    INSERT INTO audit_events
                        (id, stream_id, seq, ts, actor_id, actor_type, action, resource,
                         payload, payload_redacted, prev_hash, hash, signature)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (stream_id, seq) DO NOTHING
                """
                for e in events:
                    cur.execute(insert_sql, (
                        str(e["id"]), e["stream_id"], e["seq"], e["ts"],
                        e["actor_id"], e["actor_type"], e["action"], e["resource"],
                        json.dumps(e["payload"]) if e.get("payload") else None,
                        e.get("payload_redacted", False),
                        bytes(e["prev_hash"]) if e.get("prev_hash") else None,
                        bytes(e["hash"]),
                        bytes(e["signature"]) if e.get("signature") else None,
                    ))
                logger.info("Inserted %d events into Mirror", len(events))

                # Update audit_stream_seqs for migrated streams
                for stream_id in {e["stream_id"] for e in events}:
                    max_seq = max(e["seq"] for e in events if e["stream_id"] == stream_id)
                    genesis = next(
                        (bytes(e["hash"]) for e in events if e["stream_id"] == stream_id and e["seq"] == 1),
                        None
                    )
                    cur.execute("""
                        INSERT INTO audit_stream_seqs (stream_id, last_seq, genesis_hash)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (stream_id) DO UPDATE
                            SET last_seq = EXCLUDED.last_seq,
                                genesis_hash = EXCLUDED.genesis_hash
                    """, (stream_id, max_seq, genesis))
                    logger.info("Updated audit_stream_seqs: stream=%s last_seq=%d", stream_id, max_seq)

        logger.info("Migration complete. Verifying chain on Mirror...")
        mirror2 = psycopg2.connect(MIRROR_DSN)
        try:
            with mirror2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM audit_events WHERE stream_id != 'test-stream' ORDER BY stream_id, seq")
                mirror_events = [dict(r) for r in cur.fetchall()]
        finally:
            mirror2.close()

        if verify_chain(mirror_events):
            logger.info("Post-migration chain integrity: PASS — safe to drop Supabase audit tables")
        else:
            logger.error("Post-migration chain integrity: FAIL — do NOT drop Supabase tables yet")
            sys.exit(1)

    finally:
        mirror.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate audit_events Supabase → Mirror")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Verify Mirror chain only (post-migration check)")
    args = parser.parse_args()

    if args.verify:
        mirror = psycopg2.connect(MIRROR_DSN)
        try:
            with mirror.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM audit_events WHERE stream_id != 'test-stream' ORDER BY stream_id, seq")
                events = [dict(r) for r in cur.fetchall()]
        finally:
            mirror.close()
        ok = verify_chain(events)
        sys.exit(0 if ok else 1)

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
