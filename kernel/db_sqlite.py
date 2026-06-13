"""
SQLite + sqlite-vec backend for Mirror.

Runs on Raspberry Pi, local dev, edge — no PostgreSQL needed.
Same interface as LocalDB (kernel/db.py).

Vector dims: configurable (default 1536 for hosted providers, 384 for local ONNX models).

Usage:
    MIRROR_BACKEND=sqlite python3 mirror_api.py
    MIRROR_BACKEND=sqlite MIRROR_SQLITE_PATH=~/.mirror/mirror.db python3 mirror_api.py
    MIRROR_BACKEND=sqlite MIRROR_VECTOR_DIMS=384 python3 mirror_api.py   # local ONNX
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("mirror.db.sqlite")

def _default_sqlite_path() -> str:
    return os.getenv("MIRROR_SQLITE_PATH", str(Path.home() / ".mirror" / "mirror.db"))


def _default_vector_dims() -> int:
    return int(os.getenv("MIRROR_VECTOR_DIMS", "1536"))


def _pack(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


class SQLiteDB:
    """
    SQLite + sqlite-vec backend.

    Drop-in replacement for LocalDB — same public method signatures.
    Uses sqlite-vec (vec0 virtual table) for cosine similarity search.

    Key differences from LocalDB:
    - No psycopg2 / connection pool — stdlib sqlite3 only
    - Embeddings stored in a separate vec0 virtual table (not inline column)
    - JSON fields stored as TEXT and parsed on read
    - UUID generation via randomblob(16) instead of gen_random_uuid()
    """

    def __init__(self, db_path: str = None, dims: int = None) -> None:
        if db_path is None:
            db_path = _default_sqlite_path()
        if dims is None:
            dims = _default_vector_dims()
        self.db_path = db_path
        self.dims = dims
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Connection ────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Thread-local SQLite connection with sqlite-vec extension loaded."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            # WAL mode for concurrent reads + single writer
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")

            # Load sqlite-vec for vector search
            try:
                import sqlite_vec
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except Exception as e:
                logger.warning("sqlite-vec unavailable — vector search disabled: %s", e)

            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._conn() as conn:
            # Main engrams table — mirrors mirror_engrams PostgreSQL schema
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mirror_engrams (
                    id           TEXT PRIMARY KEY
                                 DEFAULT (lower(hex(randomblob(16)))),
                    context_id   TEXT UNIQUE NOT NULL,
                    timestamp    TEXT DEFAULT (datetime('now')),
                    series       TEXT,
                    epistemic_truths TEXT DEFAULT '[]',
                    core_concepts    TEXT DEFAULT '[]',
                    affective_vibe   TEXT DEFAULT 'Neutral',
                    energy_level     TEXT DEFAULT 'Balanced',
                    next_attractor   TEXT DEFAULT '',
                    raw_data         TEXT DEFAULT '{}',
                    project          TEXT,
                    workspace_id     TEXT,
                    owner_type       TEXT DEFAULT 'agent',
                    owner_id         TEXT,
                    importance_score REAL DEFAULT 1.0,
                    memory_tier      TEXT DEFAULT 'episodic',
                    tier             TEXT NOT NULL DEFAULT 'project',
                    entity_id        TEXT,
                    permitted_roles  TEXT DEFAULT '[]'
                )
            """)

            # Migration: add new columns to existing databases
            for col, definition in [
                ("importance_score", "REAL DEFAULT 1.0"),
                ("memory_tier",      "TEXT DEFAULT 'episodic'"),
                ("tier",             "TEXT NOT NULL DEFAULT 'project'"),
                ("entity_id",        "TEXT"),
                ("permitted_roles",  "TEXT DEFAULT '[]'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE mirror_engrams ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # column already exists

            # Backfill: set entity_id = workspace_id where entity_id is NULL
            conn.execute("""
                UPDATE mirror_engrams
                SET entity_id = workspace_id
                WHERE entity_id IS NULL AND workspace_id IS NOT NULL
            """)

            # sqlite-vec virtual table — stores embeddings alongside engram ids
            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS mirror_embeddings
                USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{self.dims}]
                )
            """)

            # Code nodes table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mirror_code_nodes (
                    id             TEXT PRIMARY KEY
                                   DEFAULT (lower(hex(randomblob(16)))),
                    node_id        TEXT NOT NULL,
                    repo           TEXT NOT NULL,
                    repo_path      TEXT NOT NULL,
                    kind           TEXT NOT NULL,
                    name           TEXT NOT NULL,
                    qualified_name TEXT,
                    file_path      TEXT NOT NULL,
                    line_start     INTEGER,
                    line_end       INTEGER,
                    language       TEXT,
                    signature      TEXT,
                    synced_at      TEXT DEFAULT (datetime('now')),
                    UNIQUE(repo_path, node_id)
                )
            """)

            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS mirror_code_embeddings
                USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{self.dims}]
                )
            """)

            # Dreamer synthesis columns (D9 + D10) — migrate existing DBs
            for col, definition in [
                ("reference_count",   "INTEGER DEFAULT 0"),
                ("archived",          "INTEGER DEFAULT 0"),  # SQLite has no BOOLEAN — 0/1
                ("consolidated_at",   "TEXT"),               # ISO datetime string or NULL
                ("synthesized",       "INTEGER DEFAULT 0"),  # 0=experienced, 1=synthesized
                ("source_engram_ids", "TEXT DEFAULT '[]'"),  # JSON array of source ids
                ("consolidated_into", "TEXT"),               # id of the consolidated engram
            ]:
                try:
                    conn.execute(f"ALTER TABLE mirror_engrams ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # column already exists

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_series ON mirror_engrams(series)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_project ON mirror_engrams(project)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_workspace ON mirror_engrams(workspace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_owner ON mirror_engrams(workspace_id, owner_type, owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_tier ON mirror_engrams(tier)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_entity_id ON mirror_engrams(entity_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_memory_tier ON mirror_engrams(memory_tier)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_consolidated_into ON mirror_engrams(consolidated_into)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_code_repo ON mirror_code_nodes(repo)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_code_kind ON mirror_code_nodes(kind)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mirror_leases (
                    lease_id     TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT '',
                    held_by      TEXT NOT NULL,
                    expires_at   TEXT NOT NULL,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (lease_id, workspace_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mirror_signals (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL DEFAULT '',
                    to_agent     TEXT NOT NULL,
                    from_agent   TEXT NOT NULL,
                    signal_name  TEXT NOT NULL,
                    payload      TEXT,
                    read_at      TEXT,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_agent ON mirror_signals(workspace_id, to_agent, id)")

        logger.info("SQLiteDB ready at %s (dims=%d)", self.db_path, self.dims)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_engram(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["epistemic_truths"] = json.loads(d.get("epistemic_truths") or "[]")
        d["core_concepts"] = json.loads(d.get("core_concepts") or "[]")
        d["raw_data"] = json.loads(d.get("raw_data") or "{}")
        d["permitted_roles"] = json.loads(d.get("permitted_roles") or "[]")
        d["source_engram_ids"] = json.loads(d.get("source_engram_ids") or "[]")
        d.setdefault("tier", "project")
        # Normalize SQLite INTEGER booleans → Python bool
        d["synthesized"] = bool(d.get("synthesized", 0))
        d["archived"] = bool(d.get("archived", 0))
        return d

    # ── Engrams ───────────────────────────────────────────────────────────────

    def upsert_engram(self, data: dict) -> dict:
        """Insert or update an engram. Embedding stored separately in vec0 table."""
        embedding: Optional[list[float]] = data.pop("embedding", None)
        context_id: str = data["context_id"]

        # Validate tier value before inserting
        tier = data.get("tier", "project")
        if tier not in {"public", "squad", "project", "entity", "private"}:
            raise ValueError(f"Invalid tier: {tier!r}. Must be one of: public, squad, project, entity, private")

        # entity_id defaults to workspace_id when not explicitly set
        entity_id = data.get("entity_id") or data.get("workspace_id")

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO mirror_engrams
                    (id, context_id, timestamp, series, epistemic_truths, core_concepts,
                     affective_vibe, energy_level, next_attractor, raw_data, project,
                     workspace_id, owner_type, owner_id, importance_score, memory_tier,
                     tier, entity_id, permitted_roles)
                VALUES (
                    coalesce(:id, lower(hex(randomblob(16)))),
                    :context_id, :timestamp, :series,
                    :epistemic_truths, :core_concepts, :affective_vibe,
                    :energy_level, :next_attractor, :raw_data, :project,
                    :workspace_id, :owner_type, :owner_id,
                    :importance_score, :memory_tier,
                    :tier, :entity_id, :permitted_roles
                )
                ON CONFLICT(context_id) DO UPDATE SET
                    series           = excluded.series,
                    epistemic_truths = excluded.epistemic_truths,
                    core_concepts    = excluded.core_concepts,
                    affective_vibe   = excluded.affective_vibe,
                    energy_level     = excluded.energy_level,
                    next_attractor   = excluded.next_attractor,
                    raw_data         = excluded.raw_data,
                    project          = excluded.project,
                    workspace_id     = excluded.workspace_id,
                    owner_type       = excluded.owner_type,
                    owner_id         = excluded.owner_id,
                    importance_score = excluded.importance_score,
                    memory_tier      = excluded.memory_tier,
                    tier             = excluded.tier,
                    entity_id        = excluded.entity_id,
                    permitted_roles  = excluded.permitted_roles
            """, {
                "id":               data.get("id"),
                "context_id":       context_id,
                "timestamp":        data.get("timestamp"),
                "series":           data.get("series", ""),
                "epistemic_truths": json.dumps(data.get("epistemic_truths", [])),
                "core_concepts":    json.dumps(data.get("core_concepts", [])),
                "affective_vibe":   data.get("affective_vibe", "Neutral"),
                "energy_level":     data.get("energy_level", "Balanced"),
                "next_attractor":   data.get("next_attractor", ""),
                "raw_data":         json.dumps(data.get("raw_data", {})),
                "project":          data.get("project"),
                "workspace_id":     data.get("workspace_id"),
                "owner_type":       data.get("owner_type", "agent"),
                "owner_id":         data.get("owner_id"),
                "importance_score": data.get("importance_score", 1.0),
                "memory_tier":      data.get("memory_tier", "episodic"),
                "tier":             tier,
                "entity_id":        entity_id,
                "permitted_roles":  json.dumps(data.get("permitted_roles") or []),
            })

            # Resolve the actual id (needed for vec0 FK)
            row = conn.execute(
                "SELECT id FROM mirror_engrams WHERE context_id = ?", (context_id,)
            ).fetchone()
            engram_id: Optional[str] = row["id"] if row else None

            # Upsert embedding — vec0 doesn't honour INSERT OR REPLACE fully;
            # DELETE + INSERT is the safe pattern for sqlite-vec 0.1.x.
            if embedding and engram_id:
                conn.execute(
                    "DELETE FROM mirror_embeddings WHERE id = ?", (engram_id,)
                )
                conn.execute(
                    "INSERT INTO mirror_embeddings(id, embedding) VALUES (?, ?)",
                    (engram_id, _pack(embedding)),
                )

        return {"context_id": context_id, "status": "ok"}

    def search_engrams(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        project: Optional[str] = None,
        series: Optional[str] = None,
        workspace_id: Optional[str] = None,
        owner_type: Optional[str] = None,
        owner_id: Optional[str] = None,
        tier_access: Optional[list[str]] = None,
        caller_entity_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Cosine similarity search via sqlite-vec.

        sqlite-vec vec0 distances are 1 - cosine_similarity for normalised
        vectors, so: similarity = 1 - distance.
        We oversample (limit * 4) to allow post-filter by metadata fields.
        """
        emb_bytes = _pack(embedding)

        with self._conn() as conn:
            # Pull top candidates from vec0 — metadata filters applied after
            oversample = limit * 4
            vec_rows = conn.execute("""
                SELECT id, distance
                FROM mirror_embeddings
                WHERE embedding MATCH ?
                  AND k = ?
                ORDER BY distance
            """, (emb_bytes, oversample)).fetchall()

            if not vec_rows:
                return []

            # Build id → distance map and filter by threshold
            id_distance: dict[str, float] = {}
            for r in vec_rows:
                similarity = 1.0 - r["distance"]
                if similarity >= threshold:
                    id_distance[r["id"]] = similarity

            if not id_distance:
                return []

            # Fetch engrams for matching ids
            placeholders = ",".join("?" * len(id_distance))
            filters = [f"e.id IN ({placeholders})"]
            params: list[Any] = list(id_distance.keys())

            # Exclude low-importance engrams (e.g. session engrams with score=0.05)
            filters.append("e.importance_score >= ?")
            params.append(0.1)  # exclude session/working-memory tier (score=0.05) regardless of similarity threshold

            if workspace_id:
                filters.append("e.workspace_id = ?")
                params.append(workspace_id)
            if project:
                filters.append("e.project = ?")
                params.append(project)
            if owner_type:
                filters.append("e.owner_type = ?")
                params.append(owner_type)
            if owner_id:
                filters.append("e.owner_id = ?")
                params.append(owner_id)
            if series:
                filters.append("e.series LIKE ?")
                params.append(f"%{series}%")

            # Tier RBAC: caller can only see tiers they have access to.
            # public is always visible.
            # entity-scoped engrams additionally require entity_id match.
            if tier_access is not None:
                accessible = list(tier_access)
                if accessible:
                    ph = ",".join("?" * len(accessible))
                    if caller_entity_id:
                        filters.append(
                            f"(e.tier = 'public' OR "
                            f"(e.tier IN ({ph}) AND (e.entity_id = ? OR e.entity_id IS NULL)))"
                        )
                        params.extend(accessible)
                        params.append(caller_entity_id)
                    else:
                        filters.append(
                            f"(e.tier = 'public' OR "
                            f"(e.tier IN ({ph}) AND e.tier != 'entity'))"
                        )
                        params.extend(accessible)
                else:
                    # Empty tier_access list → only public engrams
                    filters.append("e.tier = 'public'")

            where = " AND ".join(filters)
            rows = conn.execute(
                f"SELECT * FROM mirror_engrams e WHERE {where}", params
            ).fetchall()

            # Attach similarity scores and sort by descending similarity
            results = []
            for row in rows:
                d = self._row_to_engram(row)
                d["similarity"] = id_distance[d["id"]]
                results.append(d)

            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:limit]

    def update_engram_tier(self, engram_id: str, new_tier: str) -> Optional[dict]:
        """Update the tier of an engram. Returns the updated row or None if not found."""
        if new_tier not in {"public", "squad", "project", "entity", "private"}:
            raise ValueError(f"Invalid tier: {new_tier!r}")
        with self._conn() as conn:
            conn.execute(
                "UPDATE mirror_engrams SET tier = ? WHERE id = ?",
                (new_tier, engram_id),
            )
            row = conn.execute(
                "SELECT id, context_id, tier, workspace_id FROM mirror_engrams WHERE id = ?",
                (engram_id,),
            ).fetchone()
            return dict(row) if row else None

    def recent_engrams(
        self,
        agent: str,
        limit: int = 10,
        project: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        """Get recent engrams by series/agent name."""
        where = ["series LIKE ?"]
        params: list[Any] = [f"%{agent}%"]

        if project:
            where.append("project = ?")
            params.append(project)
        if workspace_id:
            where.append("workspace_id = ?")
            params.append(workspace_id)

        sql = (
            "SELECT * FROM mirror_engrams WHERE "
            + " AND ".join(where)
            + " ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_engram(r) for r in rows]

    def count_engrams(self, series_filter: Optional[str] = None) -> int:
        """Count engrams — optionally filtered by series."""
        with self._conn() as conn:
            if series_filter:
                return conn.execute(
                    "SELECT COUNT(*) FROM mirror_engrams WHERE series LIKE ?",
                    (f"%{series_filter}%",),
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM mirror_engrams").fetchone()[0]

    def count_engrams_in_workspace(self, workspace_id: Optional[str]) -> int:
        """Count engrams scoped to a specific workspace (safe for non-admin callers)."""
        if workspace_id is None:
            return self.count_engrams()
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM mirror_engrams WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()[0]

    def acquire_lease(
        self,
        lease_id: str,
        agent: str,
        ttl_seconds: int,
        workspace_id: Optional[str] = None,
    ) -> dict:
        ws = workspace_id or ""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT held_by, expires_at
                FROM mirror_leases
                WHERE lease_id = ? AND workspace_id = ? AND expires_at > datetime('now')
                """,
                (lease_id, ws),
            ).fetchone()
            if row:
                return {
                    "acquired": False,
                    "lease_id": lease_id,
                    "held_by": row["held_by"],
                    "expires_at": row["expires_at"],
                }

            expires_at = conn.execute(
                "SELECT datetime('now', ?)",
                (f"+{max(1, int(ttl_seconds))} seconds",),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO mirror_leases (lease_id, workspace_id, held_by, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lease_id, workspace_id) DO UPDATE SET
                    held_by = excluded.held_by,
                    expires_at = excluded.expires_at,
                    created_at = datetime('now')
                """,
                (lease_id, ws, agent, expires_at),
            )
            return {"acquired": True, "lease_id": lease_id, "expires_at": expires_at}

    def release_lease(
        self,
        lease_id: str,
        agent: str,
        workspace_id: Optional[str] = None,
    ) -> dict:
        ws = workspace_id or ""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM mirror_leases WHERE workspace_id = ? AND expires_at <= datetime('now')",
                (ws,),
            )
            row = conn.execute(
                "SELECT held_by FROM mirror_leases WHERE lease_id = ? AND workspace_id = ?",
                (lease_id, ws),
            ).fetchone()
            if not row:
                return {"released": True}
            if row["held_by"] != agent:
                return {"error": "not owner"}
            conn.execute(
                "DELETE FROM mirror_leases WHERE lease_id = ? AND workspace_id = ?",
                (lease_id, ws),
            )
            return {"released": True}

    def send_signal(
        self,
        to_agent: str,
        from_agent: str,
        signal_name: str,
        payload: dict,
        workspace_id: Optional[str] = None,
    ) -> dict:
        ws = workspace_id or ""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO mirror_signals (workspace_id, to_agent, from_agent, signal_name, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ws, to_agent, from_agent, signal_name, json.dumps(payload or {})),
            )
            return {"sent": True, "signal_id": str(cur.lastrowid)}

    def receive_signals(
        self,
        agent: str,
        since_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        ws = workspace_id or ""
        filters = ["workspace_id = ?", "to_agent = ?"]
        params: list[Any] = [ws, agent]
        if since_id:
            filters.append("id > ?")
            params.append(int(since_id))
        where = " AND ".join(filters)
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id, from_agent, signal_name, payload, created_at
                FROM mirror_signals
                WHERE {where}
                ORDER BY id ASC
                """,
                params,
            ).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE mirror_signals SET read_at = datetime('now') WHERE id IN ({placeholders})",
                    ids,
                )
            return [
                {
                    "signal_id": str(r["id"]),
                    "from_agent": r["from_agent"],
                    "signal_name": r["signal_name"],
                    "payload": json.loads(r["payload"] or "{}"),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    # ── Dreamer D9 + D10 ────────────────────────────────────────────────────

    def fetch_dreamable_engrams(
        self,
        days_back: int = 7,
        min_importance: float = 0.3,
        min_reference_count: int = 2,
    ) -> list[dict[str, Any]]:
        """Fetch engrams eligible for Dreamer processing.

        Returns engrams that are:
        - Not archived
        - Not system tier
        - Not already consolidated (consolidated_into IS NULL and memory_tier != 'consolidated')
        - Meet the importance/reference threshold OR are old archive candidates.

        NULL timestamps are treated as eligible (just stored, timestamp not yet set).
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT *
                FROM mirror_engrams
                WHERE (archived IS NULL OR archived = 0)
                  AND (memory_tier IS NULL OR memory_tier != 'system')
                  AND (memory_tier IS NULL OR memory_tier != 'consolidated')
                  AND (consolidated_into IS NULL OR consolidated_into = '')
                  AND (
                      -- Recent high-value (NULL timestamp = just stored = eligible)
                      (
                          (timestamp IS NULL OR timestamp >= datetime('now', ?))
                          AND (importance_score >= ? OR (reference_count IS NOT NULL AND reference_count >= ?))
                      )
                      -- Old archive candidates
                      OR (timestamp IS NOT NULL AND timestamp < datetime('now', ?))
                  )
                ORDER BY timestamp DESC
            """, (
                f"-{days_back} days", min_importance, min_reference_count,
                f"-80 days",
            )).fetchall()
            return [self._row_to_engram(r) for r in rows]

    def consolidate_engrams(
        self,
        days_back: int = 7,
        min_importance: float = 0.5,
        min_reference_count: int = 3,
        archive_days: int = 80,
    ) -> dict[str, Any]:
        """D9: promote recent high-value → memory_tier='consolidated';
        archive old low-value → archived=True (flag only, row kept, reversible).

        PROMOTE guard: consolidated_at IS NULL (idempotent).
        ARCHIVE guard: archived=0 (idempotent).
        Fail-safe: per-engram errors collected, not fatal.
        NO hard-delete under any path.
        """
        now_iso = __import__("datetime").datetime.utcnow().isoformat()
        promoted: list[str] = []
        archived: list[str] = []
        errors: list[dict] = []

        with self._conn() as conn:
            # Fetch candidates in one pass
            rows = conn.execute("""
                SELECT id, context_id, timestamp, memory_tier,
                       importance_score, reference_count, archived, consolidated_at
                FROM mirror_engrams
                WHERE (memory_tier IS NULL OR memory_tier != 'system')
            """).fetchall()

            for row in rows:
                rid = row["id"]
                ctx = row["context_id"]
                try:
                    ts_str = row["timestamp"] or ""
                    archived_flag = bool(row["archived"])
                    cons_at = row["consolidated_at"]
                    importance = float(row["importance_score"] or 0)
                    ref_count = int(row["reference_count"] or 0)

                    # Parse timestamp (SQLite stores as ISO string)
                    from datetime import datetime, timezone, timedelta
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        ts_naive = ts.replace(tzinfo=None)
                    except Exception:
                        ts_naive = None

                    now_naive = datetime.utcnow()
                    cutoff_recent = now_naive - timedelta(days=days_back)
                    cutoff_archive = now_naive - timedelta(days=archive_days)

                    # PROMOTE: recent high-value, not yet consolidated
                    if (
                        ts_naive is not None
                        and ts_naive >= cutoff_recent
                        and (importance >= min_importance or ref_count >= min_reference_count)
                        and not cons_at  # idempotent guard
                    ):
                        conn.execute(
                            """UPDATE mirror_engrams
                               SET memory_tier = 'consolidated', consolidated_at = ?
                               WHERE id = ?""",
                            (now_iso, rid),
                        )
                        promoted.append(ctx)

                    # ARCHIVE: old, low-value, not yet archived, not already promoted here
                    elif (
                        ts_naive is not None
                        and ts_naive < cutoff_archive
                        and not archived_flag
                        and not cons_at  # don't archive if we just promoted
                    ):
                        conn.execute(
                            "UPDATE mirror_engrams SET archived = 1 WHERE id = ?",
                            (rid,),
                        )
                        archived.append(ctx)

                except Exception as exc:
                    errors.append({"context_id": ctx, "error": str(exc)})

        return {"promoted": promoted, "archived": archived, "errors": errors}

    def synthesize_engrams(
        self,
        min_cluster_size: int = 2,
        workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """D10: cluster dreamable engrams by (series, workspace_id), produce one
        consolidated engram per cluster via rule-based merge.

        Clustering: deterministic grouping by (series, workspace_id).
        Exclude: archived, system, already-consolidated (memory_tier='consolidated'
          or consolidated_into set).
        Per-cluster consolidated engram:
          - epistemic_truths = dedup union of source values (order preserved, strings verbatim)
          - core_concepts    = dedup union of source values
          - text_digest      = source raw_data texts joined by ' ||| '
          - affective_vibe   = majority-vote across sources; None if no source carries one
          - energy_level     = majority-vote; None if no source carries one
          - next_attractor   = first non-empty source value or None
          - source_engram_ids = COMPLETE list of source IDs (ValueError if empty)
          - memory_tier = 'consolidated', synthesized=1
        Sources: consolidated_into = new engram id (demote-not-delete, reversible).
        ATOMIC: insert consolidated + mark sources in single transaction.
        Idempotent: re-run no-op (check cluster already has consolidated engram).
        Fail-safe: per-cluster errors collected.
        """
        import secrets as _secrets
        from collections import Counter

        synthesized_count = 0
        clusters_processed = 0
        errors: list[dict] = []

        # Fetch ALL non-archived, non-system, non-consolidated engrams as D10 candidates.
        # D10 clustering is based on series+workspace grouping, not importance thresholds.
        # We fetch with very permissive thresholds and a far days_back to get everything.
        candidates = self.fetch_dreamable_engrams(
            days_back=365 * 100,  # effectively all time
            min_importance=0.0,
            min_reference_count=0,
        )

        # Group by (series, workspace_id)
        cluster_map: dict[tuple, list[dict]] = {}
        for eng in candidates:
            key = (eng.get("series") or "", eng.get("workspace_id") or "")
            cluster_map.setdefault(key, []).append(eng)

        for (series, ws_id), members in cluster_map.items():
            # Apply workspace filter if requested
            if workspace_id is not None and ws_id != workspace_id:
                continue
            # Minimum cluster size
            if len(members) < min_cluster_size:
                continue

            clusters_processed += 1
            source_ids = [m["id"] for m in members]

            # Idempotent: check if this cluster already has a consolidated engram
            # by verifying no existing engram has consolidated_into pointing to these sources.
            # Simpler: check if any source already has consolidated_into set.
            already_done = any(m.get("consolidated_into") for m in members)
            if already_done:
                continue

            try:
                # Provenance guard: source_engram_ids must be non-empty
                if not source_ids:
                    raise ValueError(
                        "source_engram_ids is empty — cannot create consolidated engram "
                        "without provenance. This would be fabrication."
                    )

                # Rule-based merge — NO LLM
                # 1. epistemic_truths: dedup union, preserve order, verbatim strings from sources
                seen_et: set[str] = set()
                merged_epistemic: list[str] = []
                for m in members:
                    for val in (m.get("epistemic_truths") or []):
                        if val not in seen_et:
                            seen_et.add(val)
                            merged_epistemic.append(val)

                # 2. core_concepts: dedup union, preserve order, verbatim
                seen_cc: set[str] = set()
                merged_core: list[str] = []
                for m in members:
                    for val in (m.get("core_concepts") or []):
                        if val not in seen_cc:
                            seen_cc.add(val)
                            merged_core.append(val)

                # 3. text_digest: verbatim source texts joined by ' ||| '
                source_texts: list[str] = []
                for m in members:
                    raw = m.get("raw_data") or {}
                    text = raw.get("text", "") if isinstance(raw, dict) else ""
                    if text:
                        source_texts.append(text)
                text_digest = " ||| ".join(source_texts)

                # 4. affective_vibe: majority-vote across sources; None if no source carries one.
                # NEVER default to a fabricated label like 'Neutral'/'Balanced'.
                vibes = [m.get("affective_vibe") for m in members
                         if m.get("affective_vibe") is not None]
                if vibes:
                    vibe_counts = Counter(vibes)
                    merged_vibe: Optional[str] = vibe_counts.most_common(1)[0][0]
                else:
                    merged_vibe = None

                # 5. energy_level: majority-vote; None if no source carries one.
                energies = [m.get("energy_level") for m in members
                            if m.get("energy_level") is not None]
                if energies:
                    energy_counts = Counter(energies)
                    merged_energy: Optional[str] = energy_counts.most_common(1)[0][0]
                else:
                    merged_energy = None

                # 6. next_attractor: first non-empty source value or None.
                merged_next: Optional[str] = None
                for m in members:
                    val = m.get("next_attractor")
                    if val:
                        merged_next = val
                        break

                # Build consolidated engram row
                new_id = _secrets.token_hex(16)
                new_context_id = f"consolidated:{series}:{ws_id}:{new_id[:8]}"
                now_iso = __import__("datetime").datetime.utcnow().isoformat()

                # Use first source's project/workspace/tier as the consolidated engram's scope
                first = members[0]
                new_row = {
                    "id": new_id,
                    "context_id": new_context_id,
                    "timestamp": now_iso,
                    "series": series,
                    "project": first.get("project"),
                    "workspace_id": ws_id or None,
                    "owner_type": first.get("owner_type", "agent"),
                    "owner_id": first.get("owner_id"),
                    "importance_score": max(
                        (float(m.get("importance_score") or 0) for m in members),
                        default=1.0,
                    ),
                    "memory_tier": "consolidated",
                    "tier": first.get("tier", "project"),
                    "entity_id": first.get("entity_id"),
                    "permitted_roles": json.dumps([]),
                    "epistemic_truths": json.dumps(merged_epistemic),
                    "core_concepts": json.dumps(merged_core),
                    "affective_vibe": merged_vibe,       # None is fine — stored as NULL
                    "energy_level": merged_energy,        # None is fine — stored as NULL
                    "next_attractor": merged_next or "",
                    "raw_data": json.dumps({
                        "text": text_digest,
                        "synthesized": True,
                        "source_count": len(members),
                    }),
                    "synthesized": 1,
                    "source_engram_ids": json.dumps(source_ids),
                    "consolidated_at": now_iso,
                    "consolidated_into": None,
                    "archived": 0,
                    "reference_count": len(members),
                }

                # ATOMIC: insert consolidated + mark all sources in one transaction.
                with self._conn() as conn:
                    # Insert consolidated engram
                    cols = ", ".join(new_row.keys())
                    placeholders = ", ".join("?" * len(new_row))
                    conn.execute(
                        f"INSERT INTO mirror_engrams ({cols}) VALUES ({placeholders})",
                        list(new_row.values()),
                    )

                    # Mark all sources as consolidated_into=new_id (demote-not-delete)
                    if not source_ids:
                        # Provenance guard: should never happen here but be explicit
                        raise ValueError("source_engram_ids became empty before atomic mark — aborting")
                    ph = ",".join("?" * len(source_ids))
                    conn.execute(
                        f"UPDATE mirror_engrams SET consolidated_into = ? WHERE id IN ({ph})",
                        [new_id] + source_ids,
                    )

                synthesized_count += 1

            except Exception as exc:
                errors.append({"series": series, "workspace_id": ws_id, "error": str(exc)})

        return {
            "clusters_processed": clusters_processed,
            "synthesized": synthesized_count,
            "errors": errors,
        }

    def get_stats(self) -> dict[str, int]:
        """Engram counts grouped by series (used by health endpoint)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT series, COUNT(*) as n
                FROM mirror_engrams
                GROUP BY series
                ORDER BY n DESC
            """).fetchall()
            return {(r["series"] or "unknown"): r["n"] for r in rows}

    # ── Code nodes ───────────────────────────────────────────────────────────

    def upsert_code_nodes(self, rows: list[dict]) -> None:
        """Bulk upsert code nodes with their embeddings."""
        with self._conn() as conn:
            for row in rows:
                embedding: Optional[list[float]] = row.pop("embedding", None)
                conn.execute("""
                    INSERT INTO mirror_code_nodes
                        (node_id, repo, repo_path, kind, name, qualified_name,
                         file_path, line_start, line_end, language, signature)
                    VALUES
                        (:node_id, :repo, :repo_path, :kind, :name, :qualified_name,
                         :file_path, :line_start, :line_end, :language, :signature)
                    ON CONFLICT(repo_path, node_id) DO UPDATE SET
                        kind           = excluded.kind,
                        name           = excluded.name,
                        qualified_name = excluded.qualified_name,
                        file_path      = excluded.file_path,
                        line_start     = excluded.line_start,
                        line_end       = excluded.line_end,
                        language       = excluded.language,
                        signature      = excluded.signature,
                        synced_at      = datetime('now')
                """, row)

                if embedding:
                    r = conn.execute(
                        "SELECT id FROM mirror_code_nodes WHERE repo_path=? AND node_id=?",
                        (row["repo_path"], row["node_id"]),
                    ).fetchone()
                    if r:
                        # DELETE + INSERT — safe upsert for sqlite-vec 0.1.x
                        conn.execute(
                            "DELETE FROM mirror_code_embeddings WHERE id = ?", (r["id"],)
                        )
                        conn.execute(
                            "INSERT INTO mirror_code_embeddings(id, embedding) VALUES (?, ?)",
                            (r["id"], _pack(embedding)),
                        )

    def search_code_nodes(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        repo: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> list[dict]:
        """Cosine similarity search over code nodes."""
        emb_bytes = _pack(embedding)

        with self._conn() as conn:
            vec_rows = conn.execute("""
                SELECT id, distance
                FROM mirror_code_embeddings
                WHERE embedding MATCH ?
                  AND k = ?
                ORDER BY distance
            """, (emb_bytes, limit * 4)).fetchall()

            if not vec_rows:
                return []

            id_distance = {
                r["id"]: (1.0 - r["distance"])
                for r in vec_rows
                if (1.0 - r["distance"]) >= threshold
            }
            if not id_distance:
                return []

            placeholders = ",".join("?" * len(id_distance))
            filters = [f"n.id IN ({placeholders})"]
            params: list[Any] = list(id_distance.keys())

            if repo:
                filters.append("n.repo = ?")
                params.append(repo)
            if kind:
                filters.append("n.kind = ?")
                params.append(kind)

            rows = conn.execute(
                f"SELECT * FROM mirror_code_nodes n WHERE {' AND '.join(filters)}", params
            ).fetchall()

            results = []
            for row in rows:
                d = dict(row)
                d["similarity"] = id_distance[d["id"]]
                results.append(d)

            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:limit]

    def code_node_counts(self) -> tuple[int, dict]:
        """Total code nodes and count per repo."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT repo, COUNT(*) as n FROM mirror_code_nodes GROUP BY repo"
            ).fetchall()
            by_repo = {r["repo"]: r["n"] for r in rows}
            return sum(by_repo.values()), by_repo

    # ── Generic table interface (Supabase-compat shim) ────────────────────────

    def table(self, name: str) -> "_SQLiteTable":
        """
        Returns a chainable query builder that mirrors the Supabase .table() API.
        Allows code that calls db.table('mirror_engrams').select(...).execute()
        to work without modification.
        """
        return _SQLiteTable(self, name)


# ---------------------------------------------------------------------------
# Supabase-compatible query builder for SQLiteDB
# ---------------------------------------------------------------------------

class _SQLiteTable:
    """Chainable query builder over SQLite — mirrors _LocalTable in db.py."""

    def __init__(self, db: SQLiteDB, table: str) -> None:
        self._db = db
        self._table = table
        self._method = "select"
        self._columns = "*"
        self._filters: list[tuple] = []
        self._order: Optional[str] = None
        self._limit: Optional[int] = None
        self._data: Any = None
        self._on_conflict: Optional[str] = None
        self._single: bool = False

    def select(self, columns: str = "*", count: Optional[str] = None) -> "_SQLiteTable":
        self._method = "select"
        self._columns = columns
        return self

    def insert(self, data: dict | list[dict]) -> "_SQLiteTable":
        self._method = "insert"
        self._data = data
        return self

    def upsert(self, data: dict | list[dict], on_conflict: str = "") -> "_SQLiteTable":
        self._method = "upsert"
        self._data = data
        self._on_conflict = on_conflict
        return self

    def update(self, data: dict[str, Any]) -> "_SQLiteTable":
        self._method = "update"
        self._data = data
        return self

    def eq(self, column: str, value: Any) -> "_SQLiteTable":
        self._filters.append(("eq", column, value))
        return self

    def ilike(self, column: str, pattern: str) -> "_SQLiteTable":
        self._filters.append(("ilike", column, pattern))
        return self

    def in_(self, column: str, values: list[Any]) -> "_SQLiteTable":
        self._filters.append(("in", column, values))
        return self

    @property
    def not_(self) -> "_NotProxy":
        return _NotProxy(self)

    def order(self, column: str, desc: bool = False) -> "_SQLiteTable":
        direction = "DESC" if desc else "ASC"
        self._order = f"{column} {direction}"
        return self

    def limit(self, size: int) -> "_SQLiteTable":
        self._limit = size
        return self

    def single(self) -> "_SQLiteTable":
        self._single = True
        return self

    def execute(self) -> Any:
        from kernel.db import QueryResponse  # same dataclass

        with self._db._conn() as conn:
            if self._method == "select":
                return self._exec_select(conn, QueryResponse)
            elif self._method == "insert":
                return self._exec_insert(conn, QueryResponse)
            elif self._method == "upsert":
                return self._exec_upsert(conn, QueryResponse)
            elif self._method == "update":
                return self._exec_update(conn, QueryResponse)
            else:
                raise NotImplementedError(f"Method {self._method} not implemented")

    def _build_where(self, params: list) -> str:
        clauses = []
        for f_type, col, val in self._filters:
            if f_type == "eq":
                clauses.append(f"{col} = ?")
                params.append(val)
            elif f_type == "ilike":
                clauses.append(f"{col} LIKE ?")
                params.append(val)  # caller supplies %-pattern
            elif f_type == "in":
                ph = ",".join("?" * len(val))
                clauses.append(f"{col} IN ({ph})")
                params.extend(val)
            elif f_type == "not_in":
                ph = ",".join("?" * len(val))
                clauses.append(f"{col} NOT IN ({ph})")
                params.extend(val)
        return (" WHERE " + " AND ".join(clauses)) if clauses else ""

    def _exec_select(self, conn: sqlite3.Connection, QR: type) -> Any:
        params: list = []
        sql = f"SELECT {self._columns} FROM {self._table}"
        sql += self._build_where(params)
        if self._order:
            sql += f" ORDER BY {self._order}"
        if self._limit:
            sql += f" LIMIT {self._limit}"
        rows = conn.execute(sql, params).fetchall()
        data = [dict(r) for r in rows]
        if self._single:
            if len(data) != 1:
                raise ValueError(f"Expected 1 row, got {len(data)}")
            return QR(data=data[0])
        return QR(data=data)

    def _exec_insert(self, conn: sqlite3.Connection, QR: type) -> Any:
        row = dict(self._data) if isinstance(self._data, dict) else self._data
        if isinstance(row, list):
            return QR(data=[])  # bulk insert not needed yet
        row = self._serialize_json(row)
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        sql = f"INSERT INTO {self._table} ({cols}) VALUES ({ph}) RETURNING *"
        result = conn.execute(sql, list(row.values())).fetchone()
        return QR(data=[dict(result)] if result else [])

    def _exec_upsert(self, conn: sqlite3.Connection, QR: type) -> Any:
        if not self._on_conflict:
            raise ValueError("Upsert requires on_conflict")
        row = self._serialize_json(dict(self._data))
        conflict_cols = [c.strip() for c in self._on_conflict.split(",")]
        updates = ", ".join(
            f"{k} = excluded.{k}" for k in row if k not in conflict_cols
        )
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        sql = f"""
            INSERT INTO {self._table} ({cols}) VALUES ({ph})
            ON CONFLICT ({self._on_conflict}) DO UPDATE SET {updates}
            RETURNING *
        """
        result = conn.execute(sql, list(row.values())).fetchone()
        return QR(data=[dict(result)] if result else [])

    def _exec_update(self, conn: sqlite3.Connection, QR: type) -> Any:
        if not self._filters:
            raise ValueError("Update requires WHERE filters")
        set_params: list = []
        set_clauses = []
        for col, val in self._data.items():
            set_clauses.append(f"{col} = ?")
            set_params.append(val)
        where_params: list = []
        where = self._build_where(where_params)
        sql = f"UPDATE {self._table} SET {', '.join(set_clauses)}{where} RETURNING *"
        rows = conn.execute(sql, set_params + where_params).fetchall()
        data = [dict(r) for r in rows]
        if self._single and data:
            return QR(data=data[0])  # type: ignore[arg-type]
        from kernel.db import QueryResponse
        return QueryResponse(data=data)

    @staticmethod
    def _serialize_json(row: dict) -> dict:
        """Serialize list/dict values to JSON strings for SQLite TEXT columns."""
        result = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                result[k] = json.dumps(v)
            else:
                result[k] = v
        return result


class _NotProxy:
    def __init__(self, table: _SQLiteTable) -> None:
        self._table = table

    def in_(self, column: str, values: list[Any]) -> _SQLiteTable:
        self._table._filters.append(("not_in", column, values))
        return self._table
