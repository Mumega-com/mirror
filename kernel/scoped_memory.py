"""Access-scoped projection of an authored page revision into Mirror.

Tenant, project, and principal come from TokenContext and the grant table.
Caller-supplied project, agent, title, and metadata do not grant authority.
A stored engram is evidence of a write. It is not an approval.

This module is not the native governed-write path. It does not read or
relax approval expiry. The shadow decision adapter is not consulted.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

from kernel.auth import TokenContext
from kernel.receipts import build_mirror_engram_write_receipt


class ScopeDenied(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def token_has_project_scope(ctx: TokenContext) -> bool:
    return bool(ctx.project_id) and not ctx.is_admin


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _logical_key(tenant_id: str, project_id: str, slug: str) -> str:
    return f"inkwell\x1f{tenant_id}\x1f{project_id}\x1f{slug}"


def _local_vector(text: str, dims: int) -> list[float]:
    """Deterministic hash embedding. Does not call a network embedder."""
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
    rng = np.random.default_rng(seed)
    vec = np.zeros(dims, dtype=np.float32)
    for i in range(max(len(text) - 2, 0)):
        bucket = int(hashlib.md5(text[i:i + 3].encode()).hexdigest(), 16) % dims
        vec[bucket] += 1.0
    vec += rng.standard_normal(dims).astype(np.float32) * 0.01
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec.tolist()


def _raw(row: Any) -> dict[str, Any]:
    raw = row["raw_data"] if isinstance(row, dict) else row["raw_data"]
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def ensure_scope_tables(db: Any) -> None:
    with db._conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mirror_project_grants (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                can_read_private INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, project_id, principal_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mirror_local_receipts (
                idempotency_key TEXT PRIMARY KEY,
                receipt_id TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                context_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


def grant_project(
    db: Any,
    *,
    tenant_id: str,
    project_id: str,
    principal_id: str,
    can_read_private: bool = False,
) -> None:
    ensure_scope_tables(db)
    with db._conn() as conn:
        conn.execute(
            """
            INSERT INTO mirror_project_grants
                (tenant_id, project_id, principal_id, can_read_private, revoked)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT (tenant_id, project_id, principal_id) DO UPDATE SET
                can_read_private = excluded.can_read_private,
                revoked = 0
            """,
            (tenant_id, project_id, principal_id, 1 if can_read_private else 0),
        )


def revoke_project(db: Any, *, tenant_id: str, project_id: str, principal_id: str) -> None:
    ensure_scope_tables(db)
    with db._conn() as conn:
        conn.execute(
            """
            INSERT INTO mirror_project_grants
                (tenant_id, project_id, principal_id, can_read_private, revoked)
            VALUES (?, ?, ?, 0, 1)
            ON CONFLICT (tenant_id, project_id, principal_id) DO UPDATE SET
                revoked = 1
            """,
            (tenant_id, project_id, principal_id),
        )


def _scope(ctx: TokenContext) -> tuple[str, str, str]:
    tenant_id = ctx.workspace_id
    project_id = ctx.project_id
    principal_id = ctx.principal_id or ctx.owner_id
    if not tenant_id or not project_id or not principal_id:
        raise ScopeDenied("missing_trusted_scope")
    return tenant_id, project_id, principal_id


def _require_grant(db: Any, tenant_id: str, project_id: str, principal_id: str) -> dict[str, Any]:
    ensure_scope_tables(db)
    with db._conn() as conn:
        row = conn.execute(
            """
            SELECT can_read_private, revoked
            FROM mirror_project_grants
            WHERE tenant_id = ? AND project_id = ? AND principal_id = ?
            """,
            (tenant_id, project_id, principal_id),
        ).fetchone()
    if row is None:
        raise ScopeDenied("no_grant")
    if row["revoked"]:
        raise ScopeDenied("grant_revoked")
    return {"can_read_private": bool(row["can_read_private"])}


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _idempotency_key(tenant_id: str, project_id: str, slug: str, revision: int, content_hash: str) -> str:
    return hashlib.sha256(
        f"{tenant_id}|{project_id}|{slug}|{revision}|{content_hash}".encode()
    ).hexdigest()


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_data") if isinstance(row.get("raw_data"), dict) else _raw(row)
    source_ids = row.get("source_engram_ids") or []
    if isinstance(source_ids, str):
        try:
            source_ids = json.loads(source_ids)
        except json.JSONDecodeError:
            source_ids = []
    return {
        "id": row.get("id"),
        "context_id": raw.get("logical_context_id") or row.get("context_id"),
        "project": row.get("project"),
        "workspace_id": row.get("workspace_id"),
        "text": raw.get("text") or "",
        "title": raw.get("title") or "",
        "source_revision": raw.get("source_revision"),
        "content_hash": raw.get("content_hash"),
        "visibility": raw.get("visibility"),
        "approved": False,
        "synthesized": bool(row.get("synthesized", False)),
        "source_engram_ids": source_ids,
        "memory_tier": row.get("memory_tier"),
        "archived": bool(row.get("archived", False)),
        "timestamp": row.get("timestamp"),
        "similarity": row.get("similarity", 1.0),
        "tier": row.get("tier") or "project",
        "series": row.get("series") or "",
        "epistemic_truths": row.get("epistemic_truths") or [],
        "core_concepts": row.get("core_concepts") or [],
        "raw_data": raw,
    }


def _fetch_physical(db: Any, physical: str) -> Optional[dict[str, Any]]:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM mirror_engrams WHERE context_id = ?",
            (physical,),
        ).fetchone()
    if row is None:
        return None
    parsed = db._row_to_engram(row)
    return parsed


def _visibility_sql(can_read_private: bool) -> tuple[str, list[Any]]:
    if can_read_private:
        return "", []
    return (
        """
        AND (
            json_extract(raw_data, '$.visibility') IS NULL
            OR json_extract(raw_data, '$.visibility') != 'private'
            OR owner_id = ?
        )
        """,
        [],
    )


def _visible_clause(principal_id: str, can_read_private: bool) -> tuple[str, list[Any]]:
    clause, params = _visibility_sql(can_read_private)
    if clause:
        params = [principal_id]
    return clause, params


def accept_projection(db: Any, ctx: TokenContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Project one exact revision. Older replays do not replace a newer one."""
    tenant_id, project_id, principal_id = _scope(ctx)
    grant = _require_grant(db, tenant_id, project_id, principal_id)

    slug = str(payload.get("slug") or payload.get("context_id") or "").strip()
    text = payload.get("text")
    if not slug or not isinstance(text, str) or text == "":
        return {"status": "failed", "error": "invalid_projection", "approved": False}
    try:
        revision = int(payload.get("revision") or 1)
    except (TypeError, ValueError):
        return {"status": "failed", "error": "invalid_revision", "approved": False}

    visibility = payload.get("visibility") if payload.get("visibility") in {"public", "private"} else "public"
    content_hash = str(payload.get("content_hash") or _content_hash(text))
    key = str(payload.get("idempotency_key") or _idempotency_key(tenant_id, project_id, slug, revision, content_hash))
    physical = _logical_key(tenant_id, project_id, slug)
    title = str(payload.get("title") or "")

    existing_receipt = _receipt_by_key(db, key)
    existing = _fetch_physical(db, physical)
    if existing is not None:
        current = int(_raw(existing).get("source_revision") or 0)
        if current > revision:
            return {
                "status": "rejected_stale",
                "context_id": slug,
                "revision": current,
                "approved": False,
            }

    if existing_receipt is not None:
        return {
            "status": "accepted",
            "duplicate": True,
            "context_id": slug,
            "revision": revision,
            "receipt_id": existing_receipt["receipt_id"],
            "receipt": json.loads(existing_receipt["receipt_json"]),
            "approved": False,
        }

    audit: list[dict[str, Any]] = []
    if existing is not None:
        audit = list(_raw(existing).get("audit") or [])
    audit.append({"action": "accepted", "revision": revision, "at": _now()})

    # Caller agent, project, tenant, and approved flags are stored only as
    # untrusted echoes. The columns that authorize reads come from ctx.
    raw_data = {
        "text": text,
        "title": title,
        "logical_context_id": slug,
        "source_revision": revision,
        "content_hash": content_hash,
        "visibility": visibility,
        "synthesized": False,
        "approved": False,
        "source": {
            "kind": "inkwell_page",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "principal_id": principal_id,
            "slug": slug,
            "revision": revision,
        },
        "audit": audit,
        "untrusted": {
            "agent": payload.get("agent"),
            "project": payload.get("project"),
            "tenant": payload.get("tenant"),
            "approved": payload.get("approved"),
        },
    }
    data = {
        "context_id": physical,
        "timestamp": _now(),
        "series": f"{principal_id} - Agent Memory",
        "project": project_id,
        "workspace_id": tenant_id,
        "owner_type": "principal",
        "owner_id": principal_id,
        "epistemic_truths": [],
        "core_concepts": [],
        "affective_vibe": "Neutral",
        "energy_level": "Balanced",
        "next_attractor": "",
        "raw_data": raw_data,
        "embedding": _local_vector(text, int(getattr(db, "dims", 1536))),
        "tier": "private" if visibility == "private" else "project",
        "entity_id": tenant_id,
        "memory_tier": "episodic",
        "importance_score": 1.0,
        "permitted_roles": [],
    }
    db.upsert_engram(data)
    with db._conn() as conn:
        conn.execute(
            """
            UPDATE mirror_engrams
            SET archived = 0, synthesized = 0, source_engram_ids = '[]'
            WHERE context_id = ?
            """,
            (physical,),
        )

    receipt = build_mirror_engram_write_receipt(data, merged=False, actor=principal_id)
    receipt["output"]["approved"] = False
    receipt["input"]["approved"] = False
    receipt.setdefault("references", {})["approved"] = False
    receipt_id = str(uuid.uuid4())
    with db._conn() as conn:
        conn.execute(
            """
            INSERT INTO mirror_local_receipts
                (idempotency_key, receipt_id, receipt_json, context_id, tenant_id, project_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (key, receipt_id, json.dumps(receipt), physical, tenant_id, project_id),
        )
        stored = conn.execute(
            "SELECT receipt_id, receipt_json FROM mirror_local_receipts WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    # grant is checked so a private write is still attributable; read policy uses it later
    _ = grant
    return {
        "status": "accepted",
        "duplicate": False,
        "context_id": slug,
        "revision": revision,
        "receipt_id": stored["receipt_id"],
        "receipt": json.loads(stored["receipt_json"]),
        "approved": False,
        "synthesized": False,
    }


def _receipt_by_key(db: Any, key: str) -> Optional[Any]:
    ensure_scope_tables(db)
    with db._conn() as conn:
        return conn.execute(
            "SELECT receipt_id, receipt_json FROM mirror_local_receipts WHERE idempotency_key = ?",
            (key,),
        ).fetchone()


def lookup_engram(db: Any, ctx: TokenContext, logical_id: str) -> Optional[dict[str, Any]]:
    tenant_id, project_id, principal_id = _scope(ctx)
    grant = _require_grant(db, tenant_id, project_id, principal_id)
    row = _fetch_physical(db, _logical_key(tenant_id, project_id, logical_id))
    if row is None or row.get("archived"):
        return None
    if not _row_visible(row, principal_id, grant["can_read_private"]):
        return None
    return _public_row(row)


def _row_visible(row: dict[str, Any], principal_id: str, can_read_private: bool) -> bool:
    if row.get("archived"):
        return False
    visibility = _raw(row).get("visibility", "public")
    if visibility == "private" and not can_read_private and row.get("owner_id") != principal_id:
        return False
    return True


def _select_visible(db: Any, ctx: TokenContext, extra_sql: str, extra_params: list[Any], limit: int) -> list[dict[str, Any]]:
    tenant_id, project_id, principal_id = _scope(ctx)
    grant = _require_grant(db, tenant_id, project_id, principal_id)
    clause, vis_params = _visible_clause(principal_id, grant["can_read_private"])
    sql = f"""
        SELECT * FROM mirror_engrams
        WHERE workspace_id = ? AND project = ?
          AND (archived IS NULL OR archived = 0)
          {clause}
          {extra_sql}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params: list[Any] = [tenant_id, project_id, *vis_params, *extra_params, limit]
    with db._conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_public_row(db._row_to_engram(row)) for row in rows]


def scoped_search(db: Any, ctx: TokenContext, query: str, limit: int = 10) -> list[dict[str, Any]]:
    needle = f"%{query}%"
    return _select_visible(
        db,
        ctx,
        """
        AND (
            json_extract(raw_data, '$.text') LIKE ?
            OR json_extract(raw_data, '$.title') LIKE ?
            OR json_extract(raw_data, '$.logical_context_id') = ?
        )
        """,
        [needle, needle, query],
        limit,
    )


def scoped_recent(db: Any, ctx: TokenContext, limit: int = 10) -> list[dict[str, Any]]:
    return _select_visible(db, ctx, "", [], limit)


def scoped_count(db: Any, ctx: TokenContext) -> int:
    tenant_id, project_id, principal_id = _scope(ctx)
    grant = _require_grant(db, tenant_id, project_id, principal_id)
    clause, vis_params = _visible_clause(principal_id, grant["can_read_private"])
    sql = f"""
        SELECT COUNT(*) FROM mirror_engrams
        WHERE workspace_id = ? AND project = ?
          AND (archived IS NULL OR archived = 0)
          {clause}
    """
    with db._conn() as conn:
        return int(conn.execute(sql, [tenant_id, project_id, *vis_params]).fetchone()[0])


def withdraw_engram(db: Any, ctx: TokenContext, logical_id: str) -> dict[str, Any]:
    tenant_id, project_id, principal_id = _scope(ctx)
    _require_grant(db, tenant_id, project_id, principal_id)
    physical = _logical_key(tenant_id, project_id, logical_id)
    existing = _fetch_physical(db, physical)
    if existing is None:
        return {"status": "not_found", "approved": False}
    raw = _raw(existing)
    audit = list(raw.get("audit") or [])
    audit.append({"action": "withdrawn", "revision": raw.get("source_revision"), "at": _now()})
    raw["audit"] = audit
    raw["approved"] = False
    with db._conn() as conn:
        conn.execute(
            "UPDATE mirror_engrams SET archived = 1, raw_data = ? WHERE context_id = ?",
            (json.dumps(raw), physical),
        )
    return {"status": "withdrawn", "context_id": logical_id, "approved": False}


def read_audit(db: Any, ctx: TokenContext, logical_id: str) -> Optional[dict[str, Any]]:
    """Audit remains readable to the grant holder after withdraw. Revocation hides it."""
    tenant_id, project_id, principal_id = _scope(ctx)
    try:
        grant = _require_grant(db, tenant_id, project_id, principal_id)
    except ScopeDenied:
        return None
    row = _fetch_physical(db, _logical_key(tenant_id, project_id, logical_id))
    if row is None:
        return None
    if not row.get("archived") and not _row_visible(row, principal_id, grant["can_read_private"]):
        return None
    raw = _raw(row)
    with db._conn() as conn:
        receipts = conn.execute(
            """
            SELECT receipt_id, receipt_json FROM mirror_local_receipts
            WHERE context_id = ? AND tenant_id = ? AND project_id = ?
            """,
            (_logical_key(tenant_id, project_id, logical_id), tenant_id, project_id),
        ).fetchall()
    return {
        "context_id": logical_id,
        "archived": bool(row.get("archived")),
        "text": raw.get("text") or "",
        "audit": raw.get("audit") or [],
        "approved": False,
        "receipts": [json.loads(item["receipt_json"]) for item in receipts],
    }


def receipt_count(db: Any, ctx: TokenContext, logical_id: str) -> int:
    tenant_id, project_id, _principal_id = _scope(ctx)
    _require_grant(db, tenant_id, project_id, _principal_id)
    with db._conn() as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM mirror_local_receipts
                WHERE context_id = ? AND tenant_id = ? AND project_id = ?
                """,
                (_logical_key(tenant_id, project_id, logical_id), tenant_id, project_id),
            ).fetchone()[0]
        )
