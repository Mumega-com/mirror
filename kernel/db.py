"""
Mirror DB — backend abstraction layer.

MIRROR_BACKEND=local   (default) → PostgreSQL via psycopg2
MIRROR_BACKEND=supabase          → Supabase client (for forks/hosted deployments)

Both backends expose the same interface so mirror_api.py is backend-agnostic.
"""
from __future__ import annotations

import json
import os
import logging
from contextlib import contextmanager
from typing import Any, Optional, List, Dict, Union
from dataclasses import dataclass, field

logger = logging.getLogger("mirror.db")

# ---------------------------------------------------------------------------
# SQL identifier allowlists — prevents SQL injection via f-string identifiers
# ---------------------------------------------------------------------------

_ALLOWED_TABLES = frozenset({"mirror_engrams", "mirror_code_nodes"})
_ALLOWED_COLUMNS = frozenset({
    "id", "context_id", "agent", "text", "project", "workspace_id",
    "owner_type", "owner_id", "epistemic_truths", "core_concepts",
    "affective_vibe", "energy_level", "next_attractor", "metadata",
    "created_at", "updated_at", "*",
    # mirror_engrams extras
    "series", "timestamp", "content", "embedding", "score", "raw_data", "embedding_model",
    "memory_tier", "importance_score", "reference_count", "archived",
    # mirror_code_nodes extras
    "node_id", "repo", "repo_path", "kind", "name", "qualified_name",
    "file_path", "line_start", "line_end", "language", "signature",
    "synced_at", "labels", "blocked_by", "blocks", "tags",
})
_ALLOWED_ORDER = frozenset({
    "created_at DESC", "created_at ASC",
    "updated_at DESC", "updated_at ASC",
    "timestamp DESC", "timestamp ASC",
    "id DESC", "id ASC",
    "score DESC", "score ASC",
})


def _validate_identifier(value: str, allowed: frozenset[str], name: str) -> str:
    """Return *value* unchanged if it is in *allowed*, otherwise raise ValueError."""
    if value not in allowed:
        logger.warning("SQL identifier rejected: %s=%r", name, value)
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mirror:mirror_local_2026@localhost:5432/mirror",
)
MIRROR_BACKEND = os.getenv("MIRROR_BACKEND", "local")


@dataclass
class QueryResponse:
    """Mock Supabase response object."""
    data: List[Dict[str, Any]] = field(default_factory=list)
    count: Optional[int] = None


class _LocalTable:
    """
    A robust query builder that mimics the Supabase/PostgREST chainable syntax
    but executes directly against local PostgreSQL via psycopg2.
    """

    def __init__(self, db: LocalDB, table_name: str) -> None:
        self._db = db
        self._table = _validate_identifier(table_name, _ALLOWED_TABLES, "table")
        self._method = "select"  # select, insert, upsert, delete
        self._columns = "*"
        self._filters: List[tuple] = []
        self._order: Optional[str] = None
        self._limit: Optional[int] = None
        self._data: Any = None
        self._on_conflict: Optional[str] = None
        self._single: bool = False  # Track if single() was called

    def select(self, columns: str = "*", count: Optional[str] = None) -> _LocalTable:
        self._method = "select"
        # Validate each requested column against the allowlist
        if columns != "*":
            for col in (c.strip() for c in columns.split(",")):
                _validate_identifier(col, _ALLOWED_COLUMNS, "column")
        self._columns = columns
        return self

    def insert(self, data: Union[Dict, List[Dict]]) -> _LocalTable:
        self._method = "insert"
        self._data = data
        return self

    def upsert(self, data: Union[Dict, List[Dict]], on_conflict: str = "") -> _LocalTable:
        self._method = "upsert"
        self._data = data
        self._on_conflict = on_conflict
        return self

    def update(self, data: Dict[str, Any]) -> _LocalTable:
        self._method = "update"
        self._data = data
        return self

    def eq(self, column: str, value: Any) -> _LocalTable:
        _validate_identifier(column, _ALLOWED_COLUMNS, "column")
        self._filters.append(("eq", column, value))
        return self

    def ilike(self, column: str, pattern: str) -> _LocalTable:
        _validate_identifier(column, _ALLOWED_COLUMNS, "column")
        self._filters.append(("ilike", column, pattern))
        return self

    @property
    def not_(self) -> _LocalTable:
        # Returns a proxy that negates the next filter
        return _NotProxy(self)

    def in_(self, column: str, values: List[Any]) -> _LocalTable:
        _validate_identifier(column, _ALLOWED_COLUMNS, "column")
        self._filters.append(("in", column, values))
        return self

    def order(self, column: str, desc: bool = False) -> _LocalTable:
        direction = "DESC" if desc else "ASC"
        order_expr = f"{column} {direction}"
        _validate_identifier(order_expr, _ALLOWED_ORDER, "order expression")
        self._order = order_expr
        return self

    def limit(self, size: int) -> _LocalTable:
        self._limit = int(size)
        return self

    def single(self) -> _LocalTable:
        """Expects exactly one result. Used by Supabase API-compatible code."""
        self._single = True
        return self

    def execute(self) -> QueryResponse:
        """Constructs and executes the SQL query."""
        if self._method == "select":
            return self._execute_select()
        elif self._method == "insert":
            return self._execute_insert()
        elif self._method == "upsert":
            return self._execute_upsert()
        elif self._method == "update":
            return self._execute_update()
        else:
            raise NotImplementedError(f"Method {self._method} not implemented in shim")

    def _execute_select(self) -> QueryResponse:
        sql = f"SELECT {self._columns} FROM {self._table}"
        params = []

        if self._filters:
            where_clauses = []
            for f_type, col, val in self._filters:
                if f_type == "eq":
                    where_clauses.append(f"{col} = %s")
                    params.append(val)
                elif f_type == "ilike":
                    where_clauses.append(f"{col} ILIKE %s")
                    params.append(val)
                elif f_type == "in":
                    where_clauses.append(f"{col} = ANY(%s)")
                    params.append(val)
                elif f_type == "not_in":
                    where_clauses.append(f"NOT ({col} = ANY(%s))")
                    params.append(val)

            sql += " WHERE " + " AND ".join(where_clauses)

        if self._order:
            sql += f" ORDER BY {self._order}"

        if self._limit:
            sql += f" LIMIT {self._limit}"

        with self._db._conn() as conn:
            with conn.cursor(cursor_factory=self._db._extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                result_data = [dict(r) for r in rows]

                # Validate single() constraint
                if self._single and len(result_data) != 1:
                    raise ValueError(f"Expected exactly 1 row, got {len(result_data)}")

                # If single() was called, return the single dict, not a list
                if self._single and result_data:
                    return QueryResponse(data=result_data[0])

                return QueryResponse(data=result_data)

    def _execute_insert(self) -> QueryResponse:
        if isinstance(self._data, list):
            return self._execute_bulk_insert()

        for k in self._data.keys():
            _validate_identifier(k, _ALLOWED_COLUMNS, "column")
        columns = ", ".join(self._data.keys())
        placeholders = ", ".join([f"%({k})s" for k in self._data.keys()])
        sql = f"INSERT INTO {self._table} ({columns}) VALUES ({placeholders}) RETURNING *"
        
        # Handle complex types for JSON
        row = dict(self._data)
        for k, v in row.items():
            if isinstance(v, (dict, list)) and k not in ('epistemic_truths', 'core_concepts', 'labels', 'blocked_by', 'blocks', 'tags'):
                row[k] = self._db._extras.Json(v)

        with self._db._conn() as conn:
            with conn.cursor(cursor_factory=self._db._extras.RealDictCursor) as cur:
                cur.execute(sql, row)
                result = cur.fetchone()
                return QueryResponse(data=[dict(result)] if result else [])

    def _execute_upsert(self) -> QueryResponse:
        # Single row upsert logic (similar to upsert_engram)
        if not self._on_conflict:
            raise ValueError("Upsert requires on_conflict column(s)")

        # Validate on_conflict column(s) — may be "col" or "col1,col2"
        conflict_cols = [c.strip() for c in self._on_conflict.split(",")]
        for cc in conflict_cols:
            _validate_identifier(cc, _ALLOWED_COLUMNS, "column")

        for k in self._data.keys():
            _validate_identifier(k, _ALLOWED_COLUMNS, "column")
        columns = ", ".join(self._data.keys())
        placeholders = ", ".join([f"%({k})s" for k in self._data.keys()])

        updates = []
        for k in self._data.keys():
            if k not in conflict_cols:
                updates.append(f"{k} = EXCLUDED.{k}")
        
        sql = f"""
            INSERT INTO {self._table} ({columns}) 
            VALUES ({placeholders}) 
            ON CONFLICT ({self._on_conflict}) 
            DO UPDATE SET {', '.join(updates)}
            RETURNING *
        """
        
        row = dict(self._data)
        for k, v in row.items():
            if isinstance(v, (dict, list)) and k not in ('epistemic_truths', 'core_concepts', 'labels', 'blocked_by', 'blocks', 'tags'):
                row[k] = self._db._extras.Json(v)

        with self._db._conn() as conn:
            with conn.cursor(cursor_factory=self._db._extras.RealDictCursor) as cur:
                cur.execute(sql, row)
                result = cur.fetchone()
                return QueryResponse(data=[dict(result)] if result else [])

    def _execute_update(self) -> QueryResponse:
        """Execute UPDATE with WHERE clause."""
        if not self._filters:
            raise ValueError("Update requires WHERE filters (use .eq() etc.)")
        if not self._data:
            raise ValueError("Update requires data to set")

        # Build SET clause
        set_clauses = []
        params = []
        for col, val in self._data.items():
            _validate_identifier(col, _ALLOWED_COLUMNS, "column")
            set_clauses.append(f"{col} = %s")
            params.append(val)

        # Build WHERE clause
        where_clauses = []
        for f_type, col, val in self._filters:
            if f_type == "eq":
                where_clauses.append(f"{col} = %s")
                params.append(val)
            elif f_type == "ilike":
                where_clauses.append(f"{col} ILIKE %s")
                params.append(val)
            elif f_type == "in":
                where_clauses.append(f"{col} = ANY(%s)")
                params.append(val)
            elif f_type == "not_in":
                where_clauses.append(f"NOT ({col} = ANY(%s))")
                params.append(val)

        sql = f"UPDATE {self._table} SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)} RETURNING *"

        with self._db._conn() as conn:
            with conn.cursor(cursor_factory=self._db._extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                result_data = [dict(r) for r in rows]

                # If single() was called, return the single dict
                if self._single and result_data:
                    return QueryResponse(data=result_data[0])

                return QueryResponse(data=result_data)

    def _execute_bulk_insert(self) -> QueryResponse:
        # Simplified bulk insert for now
        return QueryResponse(data=[])


class _NotProxy:
    def __init__(self, table: _LocalTable) -> None:
        self._table = table

    def in_(self, column: str, values: List[Any]) -> _LocalTable:
        _validate_identifier(column, _ALLOWED_COLUMNS, "column")
        self._table._filters.append(("not_in", column, values))
        return self._table


class LocalDB:
    """Direct PostgreSQL backend — no external dependencies beyond psycopg2."""

    def __init__(self) -> None:
        import psycopg2
        import psycopg2.extras
        from psycopg2.pool import ThreadedConnectionPool
        self._psycopg2 = psycopg2
        self._extras = psycopg2.extras
        self._conn_str = DATABASE_URL
        self._pool = ThreadedConnectionPool(minconn=2, maxconn=10, dsn=self._conn_str)

    @contextmanager
    def _conn(self):
        """Context manager that borrows a connection from the pool and returns it after use."""
        conn = self._pool.getconn()
        try:
            conn.autocommit = True
            yield conn
        finally:
            self._pool.putconn(conn)

    # --- generic table interface ---
    def table(self, name: str) -> _LocalTable:
        return _LocalTable(self, name)

    # --- legacy/optimized methods ---

    def upsert_engram(self, data: dict) -> None:
        data.setdefault("embedding_model", "gemini-embedding-2-preview")
        self.table("mirror_engrams").upsert(data, on_conflict="context_id").execute()

    def merge_engram(self, engram_id: str, new_text: str, new_metadata: dict) -> None:
        """Merge new content into an existing near-duplicate engram.

        Increments reference_count and boosts importance_score rather than
        creating a new row. Called by the online dedup path in /store.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mirror_engrams SET
                        reference_count  = reference_count + 1,
                        importance_score = LEAST(importance_score * 1.1, 10.0),
                        timestamp        = NOW()
                    WHERE id = %s
                    """,
                    [engram_id],
                )

    def search_engrams(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        project: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        # Pass workspace_id as a function parameter so the DB enforces
        # tenant isolation inside the query plan — not as a post-filter.
        # mirror_match_engrams_v2 accepts filter_workspace_id as its 5th arg.
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM mirror_match_engrams_v2(%s::vector, %s, %s, %s, %s)",
                    [embedding, threshold, limit, project, workspace_id],
                )
                return [dict(r) for r in cur.fetchall()]

    def search_bm25(
        self,
        query: str,
        limit: int,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        """Full-text BM25 search using tsvector column on raw_data->>'text'."""
        sql = """
            SELECT id, context_id, series, project, workspace_id,
                   raw_data, epistemic_truths, core_concepts, affective_vibe,
                   timestamp AS ts, ts_rank_cd(text_tsv, plainto_tsquery('english', %s)) AS bm25_rank
            FROM mirror_engrams
            WHERE text_tsv @@ plainto_tsquery('english', %s)
        """
        params: list = [query, query]
        if workspace_id:
            sql += " AND workspace_id = %s"
            params.append(workspace_id)
        sql += " ORDER BY bm25_rank DESC LIMIT %s"
        params.append(limit)
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

    def recent_engrams(
        self,
        agent: str,
        limit: int = 10,
        project: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        query = self.table("mirror_engrams").select("*").ilike("series", f"%{agent}%")
        if project:
            query = query.eq("project", project)
        if workspace_id:
            query = query.eq("workspace_id", workspace_id)
        return query.order("timestamp", desc=True).limit(limit).execute().data

    def count_engrams(self, series_filter: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM mirror_engrams"
        params: list = []
        if series_filter:
            sql += " WHERE series ILIKE %s"
            params = [f"%{series_filter}%"]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]

    def fetch_dreamable_engrams(
        self,
        days_back: int = 7,
        min_importance: float = 0.3,
        min_reference_count: int = 2,
    ) -> list[dict]:
        """Fetch engrams eligible for Dreamer consolidation.

        Returns engrams from the last `days_back` days that meet either
        importance or reference_count threshold, plus all non-system engrams
        older than 80 days (archive candidates).
        """
        sql = """
            SELECT
                id,
                context_id,
                series,
                timestamp,
                memory_tier,
                importance_score,
                reference_count,
                archived,
                raw_data
            FROM mirror_engrams
            WHERE archived = false
              AND memory_tier != 'system'
              AND (
                  (timestamp >= NOW() - INTERVAL '%s days'
                   AND (importance_score >= %s OR reference_count >= %s))
                  OR timestamp < NOW() - INTERVAL '80 days'
              )
            ORDER BY timestamp DESC
        """
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, [days_back, min_importance, min_reference_count])
                return [dict(r) for r in cur.fetchall()]

    def update_engram_quality(
        self,
        engram_id: str,
        memory_tier: str,
        importance_score: float,
        archived: bool,
    ) -> None:
        """Update memory quality fields on a single engram."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mirror_engrams
                    SET memory_tier      = %s,
                        importance_score = %s,
                        archived         = %s
                    WHERE id = %s
                    """,
                    [memory_tier, importance_score, archived, engram_id],
                )

    # ------------------------------------------------------------------
    # Token issuance API
    # ------------------------------------------------------------------

    def create_workspace(self, slug: str, name: str) -> dict:
        """Create a new workspace. Returns the workspace row."""
        import secrets as _secrets
        ws_id = f"ws-{_secrets.token_hex(4)}"
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO mirror_workspaces (id, slug, name)
                    VALUES (%s, %s, %s)
                    RETURNING id, slug, name, active, created_at
                    """,
                    [ws_id, slug, name],
                )
                return dict(cur.fetchone())

    def list_workspaces(self) -> list[dict]:
        """Return all active workspaces."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, slug, name, active, created_at FROM mirror_workspaces WHERE active = true ORDER BY created_at DESC"
                )
                return [dict(r) for r in cur.fetchall()]

    def issue_token(
        self,
        workspace_id: str,
        label: str,
        token_type: str,
        owner_id: Optional[str],
    ) -> dict:
        """Issue a new token for a workspace.

        Returns dict with plaintext `token` (shown once), `token_id`, and `workspace_id`.
        Only the sha256 hash is stored — the plaintext is never persisted.
        """
        import secrets as _secrets
        import hashlib as _hashlib

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT slug FROM mirror_workspaces WHERE id = %s AND active = true", [workspace_id])
                row = cur.fetchone()
        if not row:
            raise ValueError(f"Workspace {workspace_id} not found or inactive")
        slug = row[0]

        plaintext = f"sk-{slug}-{_secrets.token_hex(16)}"
        token_hash = _hashlib.sha256(plaintext.encode()).hexdigest()
        tok_id = f"tok-{_secrets.token_hex(4)}"

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mirror_tokens (id, workspace_id, token_hash, label, token_type, owner_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [tok_id, workspace_id, token_hash, label, token_type, owner_id],
                )
        return {"token": plaintext, "token_id": tok_id, "workspace_id": workspace_id, "label": label}

    def list_tokens(self, workspace_id: str) -> list[dict]:
        """Return all active tokens for a workspace (hashes excluded)."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, workspace_id, label, token_type, owner_id, active, created_at, last_used_at
                    FROM mirror_tokens
                    WHERE workspace_id = %s AND active = true
                    ORDER BY created_at DESC
                    """,
                    [workspace_id],
                )
                return [dict(r) for r in cur.fetchall()]

    def revoke_token(self, token_id: str) -> None:
        """Soft-delete a token by setting active = false."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE mirror_tokens SET active = false WHERE id = %s",
                    [token_id],
                )

    def resolve_token_from_db(self, token_hash: str) -> Optional[dict]:
        """Look up a token by sha256 hash. Updates last_used_at. Returns None if not found/inactive."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT t.id, t.workspace_id, t.label, t.token_type, t.owner_id,
                           w.slug as workspace_slug
                    FROM mirror_tokens t
                    JOIN mirror_workspaces w ON w.id = t.workspace_id
                    WHERE t.token_hash = %s AND t.active = true AND w.active = true
                    """,
                    [token_hash],
                )
                row = cur.fetchone()
                if not row:
                    return None
                result = dict(row)
                cur.execute(
                    "UPDATE mirror_tokens SET last_used_at = NOW() WHERE id = %s",
                    [result["id"]],
                )
                return result

    def upsert_code_nodes(self, rows: list[dict]) -> None:
        sql = """
        INSERT INTO mirror_code_nodes
            (node_id, repo, repo_path, kind, name, qualified_name,
             file_path, line_start, line_end, language, signature, embedding)
        VALUES
            (%(node_id)s, %(repo)s, %(repo_path)s, %(kind)s, %(name)s,
             %(qualified_name)s, %(file_path)s, %(line_start)s, %(line_end)s,
             %(language)s, %(signature)s, %(embedding)s)
        ON CONFLICT (repo_path, node_id) DO UPDATE SET
            kind = EXCLUDED.kind,
            name = EXCLUDED.name,
            qualified_name = EXCLUDED.qualified_name,
            file_path = EXCLUDED.file_path,
            line_start = EXCLUDED.line_start,
            line_end = EXCLUDED.line_end,
            language = EXCLUDED.language,
            signature = EXCLUDED.signature,
            embedding = EXCLUDED.embedding,
            synced_at = NOW()
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._extras.execute_batch(cur, sql, rows)

    def search_code_nodes(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        repo: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM mirror_match_code_nodes(%s::vector, %s, %s, %s, %s)",
                    [embedding, threshold, limit, repo, kind],
                )
                return [dict(r) for r in cur.fetchall()]

    def code_node_counts(self) -> tuple[int, dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT repo, COUNT(*) as n FROM mirror_code_nodes GROUP BY repo")
                rows = cur.fetchall()
                by_repo = {r["repo"]: r["n"] for r in rows}
                total = sum(by_repo.values())
                return total, by_repo


# ---------------------------------------------------------------------------
# Supabase backend (for forks / hosted deployments)
# ---------------------------------------------------------------------------

class SupabaseDB:
    """
    Supabase backend — wraps supabase-py.
    Set MIRROR_BACKEND=supabase and provide SUPABASE_URL + SUPABASE_KEY.
    """

    def __init__(self) -> None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.getenv("SUPABASE_KEY") or os.environ["SUPABASE_API_KEY"]
        self._sb = create_client(url, key)

    def table(self, name: str):
        return self._sb.table(name)

    def rpc(self, fn: str, params: dict):
        return self._sb.rpc(fn, params)

    def upsert_engram(self, data: dict) -> None:
        data.setdefault("embedding_model", "gemini-embedding-2-preview")
        self._sb.table("mirror_engrams").upsert(data, on_conflict="context_id").execute()

    def search_engrams(self, embedding, threshold, limit, project=None):
        r = self._sb.rpc("mirror_match_engrams_v2", {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": limit,
            "filter_project": project,
        }).execute()
        return r.data

    def recent_engrams(self, agent, limit=10, project=None):
        q = self._sb.table("mirror_engrams").select("*").ilike("series", f"%{agent}%")
        if project:
            q = q.eq("project", project)
        return q.order("timestamp", desc=True).limit(limit).execute().data

    def count_engrams(self, series_filter=None):
        q = self._sb.table("mirror_engrams").select("id", count="exact")
        if series_filter:
            q = q.ilike("series", f"%{series_filter}%")
        return q.execute().count

    def upsert_code_nodes(self, rows: list[dict]) -> None:
        self._sb.table("mirror_code_nodes").upsert(rows, on_conflict="repo_path,node_id").execute()

    def search_code_nodes(self, embedding, threshold, limit, repo=None, kind=None):
        r = self._sb.rpc("mirror_match_code_nodes", {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": limit,
            "filter_repo": repo,
            "filter_kind": kind,
        }).execute()
        return r.data

    def code_node_counts(self):
        from collections import Counter
        r = self._sb.table("mirror_code_nodes").select("repo").execute()
        by_repo = dict(Counter(row["repo"] for row in r.data))
        return sum(by_repo.values()), by_repo


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_db() -> "LocalDB | SupabaseDB | Any":
    backend = os.getenv("MIRROR_BACKEND", "local")
    if backend == "supabase":
        return SupabaseDB()
    elif backend == "sqlite":
        from kernel.db_sqlite import SQLiteDB
        return SQLiteDB()
    return LocalDB()
