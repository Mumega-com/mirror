"""
Cloudflare (D1 + Vectorize) backend for Mirror.

Edge-native storage with zero local DB process:
  - Relational rows  → Cloudflare D1  (SQLite-compatible, served over REST)
  - Vector search     → Cloudflare Vectorize (managed ANN index, REST)

Same interface as LocalDB (kernel/db.py) and SQLiteDB (kernel/db_sqlite.py).
The query-builder chain compiles to parameterised SQL exactly like db_sqlite,
but emits it to the D1 HTTP query endpoint instead of a local sqlite3 handle.

Embedding generation is UNCHANGED — this backend reuses Mirror's existing
`kernel.embeddings.get_embedding` cascade. The CF backend changes STORAGE only.

Vector dims: configurable (default 1536 for Gemini text-embedding-004).
The Vectorize index must be created with a matching dimensionality and the
`cosine` distance metric, e.g.:

    npx wrangler vectorize create mirror-engrams --dimensions=1536 --metric=cosine

Usage:
    MIRROR_BACKEND=cloudflare \
        CF_ACCOUNT_ID=... CF_API_TOKEN=... \
        MIRROR_D1_DATABASE_ID=... MIRROR_VECTORIZE_INDEX=mirror-engrams \
        python3 mirror_api.py

    # local ONNX embeddings (384 dims) — set the index + dims to match:
    MIRROR_BACKEND=cloudflare MIRROR_VECTOR_DIMS=384 \
        MIRROR_VECTORIZE_INDEX=mirror-engrams-384 ... python3 mirror_api.py

Required environment:
    CF_ACCOUNT_ID          Cloudflare account id (32-hex)
    CF_API_TOKEN           API token with D1:Edit + Vectorize:Edit on the account
    MIRROR_D1_DATABASE_ID  D1 database uuid (the database, not the binding name)
    MIRROR_VECTORIZE_INDEX Vectorize index name for engram embeddings
Optional environment:
    MIRROR_VECTORIZE_CODE_INDEX  Vectorize index for code-node embeddings
                                 (default: "{MIRROR_VECTORIZE_INDEX}-code")
    MIRROR_VECTOR_DIMS           vector dims (default 1536; must match the index)
    CF_API_BASE                  override API base (default api.cloudflare.com)

Before first use, apply the D1 schema (see the `D1_SCHEMA` constant at the
bottom of this module, also emitted as kernel/db_cloudflare_schema.sql):

    npx wrangler d1 execute <DB_NAME> --remote --file kernel/db_cloudflare_schema.sql
    # or pipe D1_SCHEMA through the same /query REST endpoint this module uses.

--- D1 schema ---
The authoritative DDL lives in the `D1_SCHEMA` string at the bottom of this
file (SQLite dialect — D1 is SQLite). It mirrors the mirror_engrams /
mirror_code_nodes tables from db_sqlite.py, minus the sqlite-vec virtual
tables (vectors live in Vectorize, not D1).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional

logger = logging.getLogger("mirror.db.cloudflare")

_VALID_TIERS = {"public", "squad", "project", "entity", "private"}


def _default_vector_dims() -> int:
    return int(os.getenv("MIRROR_VECTOR_DIMS", "1536"))


def _env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Cloudflare backend requires {name} — set it in the environment. "
            "Required: CF_ACCOUNT_ID, CF_API_TOKEN, MIRROR_D1_DATABASE_ID, "
            "MIRROR_VECTORIZE_INDEX."
        )
    return val


class CloudflareDB:
    """
    Cloudflare D1 + Vectorize backend.

    Drop-in replacement for LocalDB / SQLiteDB — same public method signatures.

    Storage split:
    - Relational rows live in D1 (SQLite over REST). Reached via the
      /d1/database/{id}/query endpoint with parameterised SQL.
    - Embeddings live in a Vectorize index, keyed by the engram id, carrying
      metadata `{workspace_id, context_id, project, owner_type, owner_id,
      tier, entity_id, importance_score}` so tenant isolation + tier RBAC are
      enforced inside the ANN query (`filter`) before any D1 row fetch.

    Key differences from SQLiteDB:
    - No local file / sqlite3 handle — every op is an HTTPS round-trip.
    - vec0 MATCH is replaced by a Vectorize topK query; the matching ids are
      then hydrated from D1.
    - JSON fields stored as TEXT in D1 and parsed on read (same as SQLite).
    - UUIDs generated client-side (uuid4) when the caller omits an id, because
      D1 has no gen_random_uuid().
    """

    def __init__(self, dims: Optional[int] = None) -> None:
        self.account_id = _env("CF_ACCOUNT_ID")
        self.api_token = _env("CF_API_TOKEN")
        self.database_id = _env("MIRROR_D1_DATABASE_ID")
        self.vectorize_index = _env("MIRROR_VECTORIZE_INDEX")
        self.code_vectorize_index = os.getenv(
            "MIRROR_VECTORIZE_CODE_INDEX", f"{self.vectorize_index}-code"
        )
        self.dims = dims if dims is not None else _default_vector_dims()
        self._api_base = os.getenv("CF_API_BASE", "https://api.cloudflare.com/client/v4")
        logger.info(
            "CloudflareDB ready (d1=%s, vectorize=%s, dims=%d)",
            self.database_id,
            self.vectorize_index,
            self.dims,
        )

    # ── REST transport ─────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        """POST JSON to the Cloudflare API and return the parsed `result`.

        Raises RuntimeError on a non-success envelope so callers fail loud.
        """
        url = f"{self._api_base}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"Cloudflare API {e.code} on {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Cloudflare API unreachable on {path}: {e}") from e
        if not payload.get("success", False):
            raise RuntimeError(f"Cloudflare API error on {path}: {payload.get('errors')}")
        return payload.get("result")

    def _d1_query(self, sql: str, params: Optional[list[Any]] = None) -> list[dict]:
        """Run one parameterised SQL statement against D1, return rows as dicts.

        Mirrors a single sqlite3 `conn.execute(sql, params).fetchall()`.
        The D1 /query endpoint returns
        `[{success, results: [...rows], meta: {...}}]`; we flatten the rows.
        """
        path = f"accounts/{self.account_id}/d1/database/{self.database_id}/query"
        result = self._post(path, {"sql": sql, "params": params or []})
        rows: list[dict] = []
        # result is a list of statement-result objects (one per ; statement).
        for stmt in result or []:
            for row in stmt.get("results", []) or []:
                rows.append(dict(row))
        return rows

    # ── Vectorize transport ─────────────────────────────────────────────────────

    def _vectorize_upsert(
        self, index: str, vector_id: str, values: list[float], metadata: dict
    ) -> None:
        """Upsert a single vector (with metadata) into a Vectorize index.

        Vectorize's bulk endpoint expects NDJSON; for a single vector we send
        one line. Upsert (vs insert) so re-storing an engram overwrites cleanly.
        """
        path = f"accounts/{self.account_id}/vectorize/v2/indexes/{index}/upsert"
        ndjson = json.dumps(
            {"id": vector_id, "values": values, "metadata": metadata}
        )
        url = f"{self._api_base}/{path}"
        req = urllib.request.Request(url, data=ndjson.encode("utf-8"), method="POST")
        req.add_header("Authorization", f"Bearer {self.api_token}")
        req.add_header("Content-Type", "application/x-ndjson")
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"Vectorize upsert {e.code} on {index}: {detail}") from e
        if not payload.get("success", False):
            raise RuntimeError(f"Vectorize upsert error on {index}: {payload.get('errors')}")

    def _vectorize_query(
        self,
        index: str,
        vector: list[float],
        top_k: int,
        metadata_filter: Optional[dict] = None,
    ) -> list[tuple[str, float]]:
        """Query a Vectorize index, return [(id, score)] ordered by score desc.

        `metadata_filter` (e.g. {"workspace_id": "ws_x"}) is applied inside the
        ANN query so cross-workspace vectors are never returned — this is the
        load-bearing tenant-isolation gate (parity with mirror_match_engrams_v2).
        Vectorize cosine `score` is already the cosine similarity in [-1, 1].
        """
        path = f"accounts/{self.account_id}/vectorize/v2/indexes/{index}/query"
        body: dict[str, Any] = {
            "vector": vector,
            "topK": top_k,
            "returnValues": False,
            "returnMetadata": "none",
        }
        if metadata_filter:
            # Vectorize equality filter: {"field": {"$eq": value}}
            body["filter"] = {k: {"$eq": v} for k, v in metadata_filter.items()}
        result = self._post(path, body)
        matches = (result or {}).get("matches", []) or []
        return [(m["id"], float(m.get("score", 0.0))) for m in matches]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_engram(row: dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["epistemic_truths"] = json.loads(d.get("epistemic_truths") or "[]")
        d["core_concepts"] = json.loads(d.get("core_concepts") or "[]")
        d["raw_data"] = json.loads(d.get("raw_data") or "{}")
        d["permitted_roles"] = json.loads(d.get("permitted_roles") or "[]")
        d.setdefault("tier", "project")
        return d

    # ── Engrams ───────────────────────────────────────────────────────────────

    def upsert_engram(self, data: dict) -> dict:
        """Insert or update an engram. Embedding stored separately in Vectorize.

        Tenant isolation: workspace_id is written to BOTH the D1 row and the
        Vectorize metadata, so semantic search can filter by workspace before
        any row is touched.
        """
        embedding: Optional[list[float]] = data.pop("embedding", None)
        context_id: str = data["context_id"]

        tier = data.get("tier", "project")
        if tier not in _VALID_TIERS:
            raise ValueError(
                f"Invalid tier: {tier!r}. Must be one of: public, squad, project, entity, private"
            )

        # entity_id defaults to workspace_id when not explicitly set
        entity_id = data.get("entity_id") or data.get("workspace_id")

        # Resolve id client-side (D1 has no gen_random_uuid()). Reuse the
        # existing id on conflict so the Vectorize key stays stable.
        existing = self._d1_query(
            "SELECT id FROM mirror_engrams WHERE context_id = ?", [context_id]
        )
        engram_id: str = (
            data.get("id") or (existing[0]["id"] if existing else uuid.uuid4().hex)
        )

        params = {
            "id": engram_id,
            "context_id": context_id,
            "timestamp": data.get("timestamp"),
            "series": data.get("series", ""),
            "epistemic_truths": json.dumps(data.get("epistemic_truths", [])),
            "core_concepts": json.dumps(data.get("core_concepts", [])),
            "affective_vibe": data.get("affective_vibe", "Neutral"),
            "energy_level": data.get("energy_level", "Balanced"),
            "next_attractor": data.get("next_attractor", ""),
            "raw_data": json.dumps(data.get("raw_data", {})),
            "project": data.get("project"),
            "workspace_id": data.get("workspace_id"),
            "owner_type": data.get("owner_type", "agent"),
            "owner_id": data.get("owner_id"),
            "importance_score": data.get("importance_score", 1.0),
            "memory_tier": data.get("memory_tier", "episodic"),
            "tier": tier,
            "entity_id": entity_id,
            "permitted_roles": json.dumps(data.get("permitted_roles") or []),
        }
        # timestamp: let D1 default fill it when caller omits it.
        ts_sql = "?" if params["timestamp"] is not None else "datetime('now')"

        self._d1_query(
            f"""
            INSERT INTO mirror_engrams
                (id, context_id, timestamp, series, epistemic_truths, core_concepts,
                 affective_vibe, energy_level, next_attractor, raw_data, project,
                 workspace_id, owner_type, owner_id, importance_score, memory_tier,
                 tier, entity_id, permitted_roles)
            VALUES (?, ?, {ts_sql}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            """,
            self._engram_param_list(params, include_timestamp=params["timestamp"] is not None),
        )

        if embedding:
            self._vectorize_upsert(
                self.vectorize_index,
                engram_id,
                embedding,
                {
                    "workspace_id": params["workspace_id"] or "",
                    "context_id": context_id,
                    "project": params["project"] or "",
                    "owner_type": params["owner_type"] or "",
                    "owner_id": params["owner_id"] or "",
                    "tier": tier,
                    "entity_id": entity_id or "",
                    "importance_score": float(params["importance_score"]),
                },
            )

        return {"context_id": context_id, "status": "ok"}

    @staticmethod
    def _engram_param_list(p: dict, include_timestamp: bool) -> list:
        ordered = [
            "id", "context_id", "timestamp", "series", "epistemic_truths",
            "core_concepts", "affective_vibe", "energy_level", "next_attractor",
            "raw_data", "project", "workspace_id", "owner_type", "owner_id",
            "importance_score", "memory_tier", "tier", "entity_id", "permitted_roles",
        ]
        if not include_timestamp:
            ordered.remove("timestamp")
        return [p[k] for k in ordered]

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
        Semantic search: Vectorize topK (workspace-filtered) → hydrate from D1.

        Vectorize cosine `score` IS the cosine similarity, so we keep matches
        with score >= threshold. We oversample (limit * 4) to allow the same
        post-filter-by-metadata logic db_sqlite uses for series / tier RBAC.

        workspace_id is pushed into the Vectorize ANN filter (tenant isolation,
        load-bearing) AND re-asserted in the D1 WHERE clause (defence in depth).
        """
        oversample = limit * 4

        # Push the cheap, high-cardinality equality filters into Vectorize so
        # cross-workspace vectors never come back.
        vec_filter: dict[str, Any] = {}
        if workspace_id:
            vec_filter["workspace_id"] = workspace_id
        if project:
            vec_filter["project"] = project
        if owner_type:
            vec_filter["owner_type"] = owner_type
        if owner_id:
            vec_filter["owner_id"] = owner_id

        matches = self._vectorize_query(
            self.vectorize_index, embedding, oversample, vec_filter or None
        )
        if not matches:
            return []

        id_distance: dict[str, float] = {
            mid: score for mid, score in matches if score >= threshold
        }
        if not id_distance:
            return []

        # Hydrate matching rows from D1, re-asserting the metadata filters in
        # SQL (defence in depth) + applying filters Vectorize can't express
        # (series LIKE, tier RBAC, importance floor).
        placeholders = ",".join("?" * len(id_distance))
        filters = [f"e.id IN ({placeholders})"]
        params: list[Any] = list(id_distance.keys())

        # Exclude low-importance engrams (session/working-memory, score=0.05)
        filters.append("e.importance_score >= ?")
        params.append(0.1)

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

        # Tier RBAC — identical semantics to db_sqlite.search_engrams.
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
                filters.append("e.tier = 'public'")

        where = " AND ".join(filters)
        rows = self._d1_query(f"SELECT * FROM mirror_engrams e WHERE {where}", params)

        results = []
        for row in rows:
            d = self._row_to_engram(row)
            d["similarity"] = id_distance[d["id"]]
            results.append(d)

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def update_engram_tier(self, engram_id: str, new_tier: str) -> Optional[dict]:
        """Update the tier of an engram. Returns the updated row or None.

        Tier also lives in Vectorize metadata; re-upsert is not needed for the
        scalar tier change because tier RBAC is re-asserted in D1 after the ANN
        query. We keep Vectorize metadata eventually-consistent on next store.
        """
        if new_tier not in _VALID_TIERS:
            raise ValueError(f"Invalid tier: {new_tier!r}")
        self._d1_query(
            "UPDATE mirror_engrams SET tier = ? WHERE id = ?", [new_tier, engram_id]
        )
        rows = self._d1_query(
            "SELECT id, context_id, tier, workspace_id FROM mirror_engrams WHERE id = ?",
            [engram_id],
        )
        return dict(rows[0]) if rows else None

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
        rows = self._d1_query(sql, params)
        return [self._row_to_engram(r) for r in rows]

    def count_engrams(self, series_filter: Optional[str] = None) -> int:
        """Count engrams — optionally filtered by series."""
        if series_filter:
            rows = self._d1_query(
                "SELECT COUNT(*) AS n FROM mirror_engrams WHERE series LIKE ?",
                [f"%{series_filter}%"],
            )
        else:
            rows = self._d1_query("SELECT COUNT(*) AS n FROM mirror_engrams")
        return int(rows[0]["n"]) if rows else 0

    def count_engrams_in_workspace(self, workspace_id: Optional[str]) -> int:
        """Count engrams scoped to a specific workspace (safe for non-admin callers)."""
        if workspace_id is None:
            return self.count_engrams()
        rows = self._d1_query(
            "SELECT COUNT(*) AS n FROM mirror_engrams WHERE workspace_id = ?",
            [workspace_id],
        )
        return int(rows[0]["n"]) if rows else 0

    def get_stats(self) -> dict[str, int]:
        """Engram counts grouped by series (used by health endpoint)."""
        rows = self._d1_query(
            """
            SELECT series, COUNT(*) AS n
            FROM mirror_engrams
            GROUP BY series
            ORDER BY n DESC
            """
        )
        return {(r.get("series") or "unknown"): int(r["n"]) for r in rows}

    # ── Code nodes ───────────────────────────────────────────────────────────

    def upsert_code_nodes(self, rows: list[dict]) -> None:
        """Bulk upsert code nodes with their embeddings (D1 row + Vectorize vec)."""
        for row in rows:
            row = dict(row)
            embedding: Optional[list[float]] = row.pop("embedding", None)
            existing = self._d1_query(
                "SELECT id FROM mirror_code_nodes WHERE repo_path = ? AND node_id = ?",
                [row.get("repo_path"), row.get("node_id")],
            )
            node_pk = existing[0]["id"] if existing else uuid.uuid4().hex
            self._d1_query(
                """
                INSERT INTO mirror_code_nodes
                    (id, node_id, repo, repo_path, kind, name, qualified_name,
                     file_path, line_start, line_end, language, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                """,
                [
                    node_pk,
                    row.get("node_id"),
                    row.get("repo"),
                    row.get("repo_path"),
                    row.get("kind"),
                    row.get("name"),
                    row.get("qualified_name"),
                    row.get("file_path"),
                    row.get("line_start"),
                    row.get("line_end"),
                    row.get("language"),
                    row.get("signature"),
                ],
            )
            if embedding:
                self._vectorize_upsert(
                    self.code_vectorize_index,
                    node_pk,
                    embedding,
                    {
                        "repo": row.get("repo") or "",
                        "kind": row.get("kind") or "",
                        "node_id": row.get("node_id") or "",
                    },
                )

    def search_code_nodes(
        self,
        embedding: list[float],
        threshold: float,
        limit: int,
        repo: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> list[dict]:
        """Cosine similarity search over code nodes (Vectorize → D1 hydrate)."""
        vec_filter: dict[str, Any] = {}
        if repo:
            vec_filter["repo"] = repo
        if kind:
            vec_filter["kind"] = kind

        matches = self._vectorize_query(
            self.code_vectorize_index, embedding, limit * 4, vec_filter or None
        )
        if not matches:
            return []

        id_distance = {mid: score for mid, score in matches if score >= threshold}
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

        rows = self._d1_query(
            f"SELECT * FROM mirror_code_nodes n WHERE {' AND '.join(filters)}", params
        )
        results = []
        for row in rows:
            d = dict(row)
            d["similarity"] = id_distance[d["id"]]
            results.append(d)
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def code_node_counts(self) -> tuple[int, dict]:
        """Total code nodes and count per repo."""
        rows = self._d1_query(
            "SELECT repo, COUNT(*) AS n FROM mirror_code_nodes GROUP BY repo"
        )
        by_repo = {r["repo"]: int(r["n"]) for r in rows}
        return sum(by_repo.values()), by_repo

    # ── Generic table interface (Supabase-compat shim) ────────────────────────

    def table(self, name: str) -> "_CloudflareTable":
        """
        Returns a chainable query builder that mirrors the Supabase .table() API,
        so code calling db.table('mirror_engrams').select(...).execute() works
        unmodified against D1.
        """
        return _CloudflareTable(self, name)


# ---------------------------------------------------------------------------
# Supabase-compatible query builder for CloudflareDB (D1)
# ---------------------------------------------------------------------------


class _CloudflareTable:
    """Chainable query builder over D1 — mirrors _SQLiteTable / _LocalTable."""

    def __init__(self, db: CloudflareDB, table: str) -> None:
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

    def select(self, columns: str = "*", count: Optional[str] = None) -> "_CloudflareTable":
        self._method = "select"
        self._columns = columns
        return self

    def insert(self, data: dict | list[dict]) -> "_CloudflareTable":
        self._method = "insert"
        self._data = data
        return self

    def upsert(self, data: dict | list[dict], on_conflict: str = "") -> "_CloudflareTable":
        self._method = "upsert"
        self._data = data
        self._on_conflict = on_conflict
        return self

    def update(self, data: dict[str, Any]) -> "_CloudflareTable":
        self._method = "update"
        self._data = data
        return self

    def eq(self, column: str, value: Any) -> "_CloudflareTable":
        self._filters.append(("eq", column, value))
        return self

    def ilike(self, column: str, pattern: str) -> "_CloudflareTable":
        self._filters.append(("ilike", column, pattern))
        return self

    def in_(self, column: str, values: list[Any]) -> "_CloudflareTable":
        self._filters.append(("in", column, values))
        return self

    @property
    def not_(self) -> "_NotProxy":
        return _NotProxy(self)

    def order(self, column: str, desc: bool = False) -> "_CloudflareTable":
        direction = "DESC" if desc else "ASC"
        self._order = f"{column} {direction}"
        return self

    def limit(self, size: int) -> "_CloudflareTable":
        self._limit = size
        return self

    def single(self) -> "_CloudflareTable":
        self._single = True
        return self

    def execute(self) -> Any:
        from kernel.db import QueryResponse  # same dataclass

        if self._method == "select":
            return self._exec_select(QueryResponse)
        elif self._method == "insert":
            return self._exec_insert(QueryResponse)
        elif self._method == "upsert":
            return self._exec_upsert(QueryResponse)
        elif self._method == "update":
            return self._exec_update(QueryResponse)
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

    def _exec_select(self, QR: type) -> Any:
        params: list = []
        sql = f"SELECT {self._columns} FROM {self._table}"
        sql += self._build_where(params)
        if self._order:
            sql += f" ORDER BY {self._order}"
        if self._limit:
            sql += f" LIMIT {self._limit}"
        data = self._db._d1_query(sql, params)
        if self._single:
            if len(data) != 1:
                raise ValueError(f"Expected 1 row, got {len(data)}")
            return QR(data=data[0])
        return QR(data=data)

    def _exec_insert(self, QR: type) -> Any:
        row = dict(self._data) if isinstance(self._data, dict) else self._data
        if isinstance(row, list):
            return QR(data=[])  # bulk insert not needed yet (parity with db_sqlite)
        row = self._serialize_json(row)
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        sql = f"INSERT INTO {self._table} ({cols}) VALUES ({ph}) RETURNING *"
        result = self._db._d1_query(sql, list(row.values()))
        return QR(data=[result[0]] if result else [])

    def _exec_upsert(self, QR: type) -> Any:
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
        result = self._db._d1_query(sql, list(row.values()))
        return QR(data=[result[0]] if result else [])

    def _exec_update(self, QR: type) -> Any:
        if not self._filters:
            raise ValueError("Update requires WHERE filters")
        set_params: list = []
        set_clauses = []
        for col, val in self._serialize_json(self._data).items():
            set_clauses.append(f"{col} = ?")
            set_params.append(val)
        where_params: list = []
        where = self._build_where(where_params)
        sql = f"UPDATE {self._table} SET {', '.join(set_clauses)}{where} RETURNING *"
        data = self._db._d1_query(sql, set_params + where_params)
        if self._single and data:
            return QR(data=data[0])  # type: ignore[arg-type]
        return QR(data=data)

    @staticmethod
    def _serialize_json(row: dict) -> dict:
        """Serialize list/dict values to JSON strings for D1 TEXT columns."""
        result = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                result[k] = json.dumps(v)
            else:
                result[k] = v
        return result


class _NotProxy:
    def __init__(self, table: _CloudflareTable) -> None:
        self._table = table

    def in_(self, column: str, values: list[Any]) -> _CloudflareTable:
        self._table._filters.append(("not_in", column, values))
        return self._table


# ---------------------------------------------------------------------------
# --- D1 schema ---  (SQLite dialect — D1 is SQLite)
#
# Apply once to the D1 database before first use:
#   npx wrangler d1 execute <DB_NAME> --remote --file kernel/db_cloudflare_schema.sql
#
# Vectors are NOT stored in D1 — they live in the Vectorize index. Create it
# with dims matching MIRROR_VECTOR_DIMS and the cosine metric:
#   npx wrangler vectorize create mirror-engrams --dimensions=1536 --metric=cosine
#   npx wrangler vectorize create mirror-engrams-code --dimensions=1536 --metric=cosine
#
# Vectorize metadata indexes (so $eq filters are usable for tenant isolation):
#   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=workspace_id --type=string
#   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=project --type=string
#   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=owner_type --type=string
#   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=owner_id --type=string
# ---------------------------------------------------------------------------

D1_SCHEMA = """
-- Mirror Cloudflare (D1) schema — SQLite dialect.
-- Mirrors mirror_engrams / mirror_code_nodes from kernel/db_sqlite.py.
-- Vectors live in Cloudflare Vectorize, NOT in D1.

CREATE TABLE IF NOT EXISTS mirror_engrams (
    id               TEXT PRIMARY KEY,            -- uuid4 hex assigned client-side
    context_id       TEXT UNIQUE NOT NULL,
    timestamp        TEXT DEFAULT (datetime('now')),
    series           TEXT,
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
);

CREATE INDEX IF NOT EXISTS idx_eng_series    ON mirror_engrams(series);
CREATE INDEX IF NOT EXISTS idx_eng_project   ON mirror_engrams(project);
CREATE INDEX IF NOT EXISTS idx_eng_workspace ON mirror_engrams(workspace_id);
CREATE INDEX IF NOT EXISTS idx_eng_owner     ON mirror_engrams(workspace_id, owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_eng_tier      ON mirror_engrams(tier);
CREATE INDEX IF NOT EXISTS idx_eng_entity_id ON mirror_engrams(entity_id);

CREATE TABLE IF NOT EXISTS mirror_code_nodes (
    id             TEXT PRIMARY KEY,              -- uuid4 hex assigned client-side
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
);

CREATE INDEX IF NOT EXISTS idx_code_repo ON mirror_code_nodes(repo);
CREATE INDEX IF NOT EXISTS idx_code_kind ON mirror_code_nodes(kind);
"""
