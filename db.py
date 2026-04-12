"""
Mirror DB — backend abstraction layer.

MIRROR_BACKEND=local   (default) → PostgreSQL via psycopg2
MIRROR_BACKEND=supabase          → Supabase client (for forks/hosted deployments)

Both backends expose the same interface so mirror_api.py is backend-agnostic.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mirror:mirror_local_2026@localhost:5432/mirror",
)
MIRROR_BACKEND = os.getenv("MIRROR_BACKEND", "local")


# ---------------------------------------------------------------------------
# Local backend (psycopg2)
# ---------------------------------------------------------------------------

class LocalDB:
    """Direct PostgreSQL backend — no external dependencies beyond psycopg2."""

    def __init__(self) -> None:
        import psycopg2
        import psycopg2.extras
        self._psycopg2 = psycopg2
        self._extras = psycopg2.extras
        self._conn_str = DATABASE_URL

    def _conn(self):
        conn = self._psycopg2.connect(self._conn_str)
        conn.autocommit = True
        return conn

    # --- engrams ---

    def upsert_engram(self, data: dict) -> None:
        sql = """
        INSERT INTO mirror_engrams
            (context_id, timestamp, series, project, epistemic_truths, core_concepts,
             affective_vibe, energy_level, next_attractor, raw_data, embedding)
        VALUES
            (%(context_id)s, %(timestamp)s, %(series)s, %(project)s,
             %(epistemic_truths)s, %(core_concepts)s, %(affective_vibe)s,
             %(energy_level)s, %(next_attractor)s, %(raw_data)s, %(embedding)s)
        ON CONFLICT (context_id) DO UPDATE SET
            series = EXCLUDED.series,
            project = EXCLUDED.project,
            epistemic_truths = EXCLUDED.epistemic_truths,
            core_concepts = EXCLUDED.core_concepts,
            affective_vibe = EXCLUDED.affective_vibe,
            energy_level = EXCLUDED.energy_level,
            next_attractor = EXCLUDED.next_attractor,
            raw_data = EXCLUDED.raw_data,
            embedding = EXCLUDED.embedding
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                row = dict(data)
                row["epistemic_truths"] = row.get("epistemic_truths") or []
                row["core_concepts"] = row.get("core_concepts") or []
                row["raw_data"] = self._extras.Json(row.get("raw_data") or {})
                cur.execute(sql, row)

    def search_engrams(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        project: Optional[str] = None,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM mirror_match_engrams_v2(%s, %s, %s, %s)",
                    [embedding, threshold, limit, project],
                )
                return [dict(r) for r in cur.fetchall()]

    def recent_engrams(
        self,
        agent: str,
        limit: int = 10,
        project: Optional[str] = None,
    ) -> list[dict]:
        sql = """
        SELECT * FROM mirror_engrams
        WHERE series ILIKE %(agent)s
        {project_filter}
        ORDER BY timestamp DESC
        LIMIT %(limit)s
        """
        project_filter = "AND project = %(project)s" if project else ""
        sql = sql.format(project_filter=project_filter)
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, {"agent": f"%{agent}%", "limit": limit, "project": project})
                return [dict(r) for r in cur.fetchall()]

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

    # --- code nodes ---

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
                    "SELECT * FROM mirror_match_code_nodes(%s, %s, %s, %s, %s)",
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

    # --- tasks ---

    def table(self, name: str) -> "_LocalTable":
        return _LocalTable(self, name)


class _LocalTable:
    """Thin shim so task_router / agent_router can call .table(...).select/upsert."""

    def __init__(self, db: LocalDB, name: str) -> None:
        self._db = db
        self._name = name

    def select(self, *_args, count: Optional[str] = None, **_kw):
        return self

    def upsert(self, data, on_conflict: str = "") -> "_LocalTable":
        self._upsert_data = data
        self._conflict = on_conflict
        return self

    def execute(self):
        # Minimal passthrough — task/agent routers handle their own SQL
        return type("R", (), {"data": [], "count": 0})()

    # filter chains — no-op shims so existing code doesn't crash
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def or_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self


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

    # Passthrough .table() so existing code works unchanged
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

def get_db() -> LocalDB | SupabaseDB:
    if MIRROR_BACKEND == "supabase":
        return SupabaseDB()
    return LocalDB()
