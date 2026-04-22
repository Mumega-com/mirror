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
        self._table = table_name
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
        self._filters.append(("eq", column, value))
        return self

    def ilike(self, column: str, pattern: str) -> _LocalTable:
        self._filters.append(("ilike", column, pattern))
        return self

    @property
    def not_(self) -> _LocalTable:
        # Returns a proxy that negates the next filter
        return _NotProxy(self)

    def in_(self, column: str, values: List[Any]) -> _LocalTable:
        self._filters.append(("in", column, values))
        return self

    def order(self, column: str, desc: bool = False) -> _LocalTable:
        direction = "DESC" if desc else "ASC"
        self._order = f"{column} {direction}"
        return self

    def limit(self, size: int) -> _LocalTable:
        self._limit = size
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
            
        columns = ", ".join(self._data.keys())
        placeholders = ", ".join([f"%({k})s" for k in self._data.keys()])
        
        updates = []
        for k in self._data.keys():
            if k not in self._on_conflict.split(','):
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
        self.table("mirror_engrams").upsert(data, on_conflict="context_id").execute()

    def search_engrams(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        project: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                if workspace_id:
                    cur.execute(
                        "SELECT * FROM mirror_match_engrams_v2(%s::vector, %s, %s, %s)"
                        " WHERE workspace_id = %s",
                        [embedding, threshold, limit, project, workspace_id],
                    )
                else:
                    cur.execute(
                        "SELECT * FROM mirror_match_engrams_v2(%s::vector, %s, %s, %s)",
                        [embedding, threshold, limit, project],
                    )
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
