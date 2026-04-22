# Changelog — Mirror

## 2026-04-22 — Microkernel + Pi Backend + Workspace Isolation + MCP Server

### Summary
Mirror went from a fragile monolith that crashed on restart to a stable, isolated, Pi-capable memory service with a proper MCP endpoint for Claude Desktop and ChatGPT. All critical bugs fixed. Architecture aligned with SOS and Inkwell patterns.

---

### Bug Fixes

**Port conflict on restart (was: crash loop)**
- `ExecStartPre=-/bin/bash -c 'fuser -k 8844/tcp || true'` added to systemd unit
- Service now kills stale process on port 8844 before starting

**NoneType crash on every task completion**
- `task_router.py` called `_openai.embeddings.create()` — `_openai` was always `None`
- Fixed: replaced with `get_embedding(text)` from the embeddings module

**No connection pooling — per-request PostgreSQL connections**
- `LocalDB._conn()` opened a new `psycopg2.connect()` on every request
- Fixed: `ThreadedConnectionPool(minconn=2, maxconn=10)` + `@contextmanager` for proper release

**Systemd restart policy too aggressive**
- `StartLimitIntervalSec` was in `[Service]` (wrong section, silently ignored)
- Fixed: moved to `[Unit]`, added `StartLimitBurst=5` — service respects cooldown now

**Circular import between mirror_api ↔ task_router**
- `mirror_api` imported `task_router`; `task_router` imported `get_embedding` from `mirror_api`
- Fixed: extracted `get_embedding` into standalone `embeddings.py` shim → `kernel/embeddings.py`

---

### Architecture — Microkernel Refactor

Mirror now follows the same microkernel pattern as SOS and Inkwell.

**`kernel/` — shared contracts and services**
- `kernel/types.py` — Pydantic models: `SearchRequest`, `EngramStoreRequest`, `EngramResponse`, `TokenContext`
- `kernel/health.py` — `HealthStatus` dataclass + `async health_check(db)`
- `kernel/embeddings.py` — 4-tier embedding cascade (see below)
- `kernel/db.py` — `LocalDB` (PostgreSQL), `SupabaseDB`, `get_db()` factory; `MIRROR_BACKEND` env var selects backend at call time
- `kernel/auth.py` — `TokenContext` dataclass + `resolve_token_context()` (new, this session)

**`plugins/` — self-contained modules**
- `plugins/manifest.py` — `PluginManifest` dataclass (name, version, routes_factory, mcp_tools, enabled)
- `plugins/loader.py` — `register()` (idempotent), `mount_all(app)`, `summary()`
- `plugins/memory/` — `/search`, `/store`, `/recent/{agent}`, `/stats` extracted from mirror_api.py
- `plugins/mcp_server/` — MCP JSON-RPC 2.0 plugin (new, this session)

**Shims for backwards compatibility**
- `db.py` → `from kernel.db import *`
- `embeddings.py` → `from kernel.embeddings import *`

**`mirror_api.py`** slimmed: registers plugins, mounts routes; inline auth and legacy endpoints kept for now.

---

### Embedding Cascade (Gemini 2 → Gemini 1 → ONNX → numpy)

Mirror never fails to embed, even without network or API key.

| Tier | Model | Dims | Cost |
|------|-------|------|------|
| 1 | `gemini-embedding-2-preview` | 1536 (MRL native) | $0.20/1M |
| 2 | `gemini-embedding-001` | 768 → padded to 1536 | $0.025/1M |
| 3 | `fastembed` BAAI/bge-small-en-v1.5 | 384 → padded to 1536 | free, local |
| 4 | numpy n-gram hash | 1536 | free, deterministic |

Upgraded from OpenAI embeddings to Gemini Embedding 2 (MRL, multimodal, same 1536 dims).

---

### Raspberry Pi / Offline Backend

Mirror can now run completely offline with zero external dependencies.

```bash
MIRROR_BACKEND=sqlite python3 mirror_api.py
MIRROR_BACKEND=sqlite MIRROR_SQLITE_PATH=/data/mirror.db python3 mirror_api.py
```

**`kernel/db_sqlite.py`**
- `SQLiteDB` — drop-in for `LocalDB`, same method signatures
- `sqlite-vec` (0.1.9) for cosine similarity vector search (vec0 virtual table)
- WAL mode for concurrent reads + single writer
- `DELETE + INSERT` pattern for vec0 upsert (sqlite-vec 0.1.x workaround)
- `mirror_embeddings` and `mirror_code_embeddings` vec0 tables (1536d)
- `_SQLiteTable` — Supabase-compatible query builder for `.table().select().execute()` callers

---

### Workspace Isolation

Hard tenant boundaries — tokens determine workspace, server enforces it.

**`kernel/auth.py`**
```python
@dataclass
class TokenContext:
    workspace_id: Optional[str]   # None = admin (sees all)
    owner_type: Optional[str]     # user | project | squad | agent
    owner_id: Optional[str]
    is_admin: bool = False
```

Resolution order:
1. Admin token → `is_admin=True`, no workspace restriction
2. `tenant_keys.json` match → scoped to `workspace_id` (defaults to `agent_slug`)
3. SOS bus token → scoped to `project`
4. Unknown → 401

**Routes updated** (`plugins/memory/routes.py`):
- `POST /store` — tags engram with `workspace_id` from token, no workarounds
- `POST /search` — hard filters by `workspace_id`; admin sees all
- `GET /recent/{agent}` — scoped to caller's workspace
- `GET /stats` — admin: global counts; tenant: workspace count

**Inkwell adapter updated** (`kernel/adapters/sos-memory.ts`):
- Removed `[tenantId]` content prefix (was soft-isolation workaround)
- Correct Mirror v2 request schema wired (`context_id`, `agent`, `text`, etc.)
- Response mapped to `MemoryPort.MemoryResult` interface
- Closes Inkwell known gap: "Mirror tenant isolation — prefix workaround"

---

### MCP Server (Claude Desktop + ChatGPT)

New plugin: `plugins/mcp_server/`

| Endpoint | Purpose |
|----------|---------|
| `POST /mcp/{token}/rpc` | JSON-RPC 2.0 — single request/response |
| `GET /mcp/{token}/sse` | SSE stream — long-lived connection (keep-alive ping every 15s) |

Token in URL path (not Authorization header) — required by Claude Desktop and ChatGPT.

**MCP tools exposed:**
- `memory_search` — semantic search, workspace-scoped
- `memory_store` — store engram, workspace-scoped
- `memory_recent` — recent engrams by agent

**Claude Desktop config:**
```json
{
  "mcpServers": {
    "mirror": {
      "url": "http://your-server:8844/mcp/YOUR_TOKEN/sse"
    }
  }
}
```

---

### Tests

22 tests added in `tests/`:
- `test_auth.py` (8) — TokenContext, admin/tenant/inactive/unknown token resolution
- `test_workspace_isolation.py` (5) — SQLite backend workspace hard boundaries
- `test_routes_workspace.py` (5) — HTTP API workspace scoping via token
- `test_mcp_server.py` (4) — tools/list, invalid token, unknown method, tools/call

---

### GitHub Issues Closed

| # | Issue |
|---|-------|
| #2 | Port conflict on restart |
| #5 | NoneType crash on task completion |
| #7 | No connection pooling |
| #8 | Systemd restart policy |
| #9 | Missing /health endpoint |
| #13 | Microkernel refactor |
| #15 | Workspace model — hard tenant isolation |
| #18 | MCP SSE server for Claude Desktop and ChatGPT |

---

### Squad + Task Board

Mirror squad registered in SOS Squad Service (`:8060`).
Token: `sk-squad-mirror-e57b81478753ee8e47b344e8a8fb9433` (store in `.env.secrets`).

**Open backlog (12 tasks):**

| Priority | ID | Title |
|----------|----|-------|
| critical | mirror-backfill-workspaces | Backfill existing engrams into default workspace |
| critical | mirror-token-issuance-api | Workspace token issuance API |
| high | mirror-hybrid-search | Hybrid search — BM25 + vector + RRF reranking |
| high | mirror-user-personal-mirrors | User personal mirrors — isolated per-user memory |
| high | mirror-project-squad-collections | Project and squad memory collections |
| high | mirror-halfvec-migration | halfvec migration — 50% storage reduction |
| medium | mirror-three-source-blending | Three-source blending — semantic + frequency + recency |
| medium | mirror-temporal-entity-layer | Temporal/entity layer — facts age and contradict |
| medium | mirror-search-first-retrieval | Search-first retrieval tools — grep → describe → expand → synthesize |
| medium | mirror-sensitivity-forgetting | Sensitivity scores + forgetting policy |
| low | mirror-surprisal-metric | Surprisal metric for engram prioritization |
| low | mirror-dreamer-specialists | Dreamer specialist agents — deduction + induction |

---

## Previous

See `git log` for history prior to 2026-04-22.
