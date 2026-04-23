"""
SQLite + sqlite-vec backend for Mirror.

Runs on Raspberry Pi, local dev, edge — no PostgreSQL needed.
Same interface as LocalDB (kernel/db.py).

Vector dims: configurable (default 1536 for Gemini, 384 for local ONNX models).

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
                    memory_tier      TEXT DEFAULT 'episodic'
                )
            """)

            # Migration: add new columns to existing databases
            for col, definition in [
                ("importance_score", "REAL DEFAULT 1.0"),
                ("memory_tier",      "TEXT DEFAULT 'episodic'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE mirror_engrams ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # column already exists

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

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_series ON mirror_engrams(series)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_project ON mirror_engrams(project)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_workspace ON mirror_engrams(workspace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_owner ON mirror_engrams(workspace_id, owner_type, owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_code_repo ON mirror_code_nodes(repo)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_code_kind ON mirror_code_nodes(kind)")

        logger.info("SQLiteDB ready at %s (dims=%d)", self.db_path, self.dims)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_engram(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["epistemic_truths"] = json.loads(d.get("epistemic_truths") or "[]")
        d["core_concepts"] = json.loads(d.get("core_concepts") or "[]")
        d["raw_data"] = json.loads(d.get("raw_data") or "{}")
        return d

    # ── Engrams ───────────────────────────────────────────────────────────────

    def upsert_engram(self, data: dict) -> dict:
        """Insert or update an engram. Embedding stored separately in vec0 table."""
        embedding: Optional[list[float]] = data.pop("embedding", None)
        context_id: str = data["context_id"]

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO mirror_engrams
                    (id, context_id, timestamp, series, epistemic_truths, core_concepts,
                     affective_vibe, energy_level, next_attractor, raw_data, project,
                     workspace_id, owner_type, owner_id, importance_score, memory_tier)
                VALUES (
                    coalesce(:id, lower(hex(randomblob(16)))),
                    :context_id, :timestamp, :series,
                    :epistemic_truths, :core_concepts, :affective_vibe,
                    :energy_level, :next_attractor, :raw_data, :project,
                    :workspace_id, :owner_type, :owner_id,
                    :importance_score, :memory_tier
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
                    memory_tier      = excluded.memory_tier
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
            params.append(threshold)

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
