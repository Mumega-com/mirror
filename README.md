# Mirror

**Shared semantic memory for AI agent teams.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Status: Active](https://img.shields.io/badge/status-active-brightgreen.svg)]()

Mirror is a self-hostable memory layer for AI agents. Agents store episodic memories ("engrams") and retrieve them later by meaning — hybrid vector + full-text search with reciprocal rank fusion reranking. Built for multi-agent teams with workspace-level tenant isolation.

Runs standalone on a $5 VPS. Native kernel integration with [SOS](https://github.com/servathadi/mumega-docs/tree/main/architecture) — no config needed when SOS is present.

---

## Why Mirror?

| | Mirror | Mem0 | Letta | Zep |
|---|---|---|---|---|
| Shared across all agents | ✅ | ❌ per-user | ❌ per-agent | Partial |
| Self-hosted | ✅ | SaaS | Self-hosted | Hosted only |
| Hybrid search (BM25 + vector) | ✅ | ❌ | ❌ | ❌ |
| Tenant workspace isolation | ✅ DB-enforced | ❌ | ❌ | Partial |
| MCP-native | ✅ | ❌ | ❌ | ❌ |
| SOS bus integration | ✅ native | ❌ | ❌ | ❌ |
| Free to run | ✅ | Paid tiers | Compute cost | Hosted only |

The key difference: Mirror is **team memory**, not per-agent memory. When agent A stores something, agent B finds it — scoped by project workspace.

---

## Architecture

Mirror is structured as a **microkernel with an optional HTTP service** on top:

```
┌─────────────────────────────────────────────────────┐
│                  mirror/kernel/                      │
│   db.py        — PostgreSQL pool, query builder      │
│   embeddings.py — Gemini / ONNX / fallback cascade   │
│   auth.py      — token verification, workspace scope │
│   types.py     — shared Pydantic models              │
└─────────────────────────────────────────────────────┘
           │ imported directly (no HTTP)
           ▼
┌─────────────────────────────────────────────────────┐
│              SOS agent bus (optional)                │
│   mcp__sos__remember → kernel.db.upsert_engram()    │
│   mcp__sos__recall   → kernel.db.search_engrams()   │
└─────────────────────────────────────────────────────┘
           │ or
           ▼
┌─────────────────────────────────────────────────────┐
│            Mirror HTTP service (:8844)               │
│   POST /store     — store engram                     │
│   POST /search    — hybrid recall                    │
│   GET  /recent    — latest by agent                  │
│   POST /code/search — code graph search              │
└─────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────┐
│         PostgreSQL + pgvector (halfvec)              │
│   mirror_engrams      — episodic memories            │
│   mirror_code_nodes   — code graph nodes             │
└─────────────────────────────────────────────────────┘
```

**Two backends:**
- `MIRROR_BACKEND=local` — PostgreSQL via psycopg2 (default, production)
- `MIRROR_BACKEND=supabase` — Supabase client (hosted deployments)

---

## Quick Start

**Requirements:** Python 3.11+, PostgreSQL 14+ with pgvector ≥ 0.7.0, Gemini API key (free at [aistudio.google.com](https://aistudio.google.com))

```bash
git clone https://github.com/Mumega-com/mirror.git
cd mirror
pip install fastapi uvicorn psycopg2-binary python-dotenv google-genai pydantic

# Set up PostgreSQL
createdb mirror
psql mirror < schema.sql
for f in migrations/*.sql; do psql mirror < "$f"; done

# Configure
cp .env.example .env
# Set GEMINI_API_KEY and DATABASE_URL

python mirror_api.py
# Live at http://localhost:8844
```

**Store a memory:**
```bash
curl -X POST http://localhost:8844/store \
  -H "Authorization: Bearer sk-your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "kasra",
    "context_id": "session_001",
    "text": "User prefers Python over TypeScript for backend services.",
    "core_concepts": ["python", "backend", "preferences"]
  }'
```

**Search memories (hybrid BM25 + vector):**
```bash
curl -X POST http://localhost:8844/search \
  -H "Authorization: Bearer sk-your-token" \
  -H "Content-Type: application/json" \
  -d '{"query": "language preferences", "top_k": 5}'
```

---

## Hybrid Search

Mirror blends two retrieval paths with **Reciprocal Rank Fusion (RRF)**:

1. **Vector search** — cosine similarity via pgvector `halfvec(1536)` HNSW index
2. **Full-text BM25** — `ts_rank_cd` over a `tsvector` GIN index on engram text

Each path fetches `top_k × 2` candidates. RRF merges the ranked lists:

```
score(doc) = Σ 1 / (k + rank + 1)   where k = 60
```

Documents appearing in both lists score higher. Final result is trimmed to `top_k`. Gracefully degrades to vector-only on backends without BM25 support.

---

## Workspace Isolation

Every engram is tagged with a `workspace_id` derived from the caller's token. Isolation is enforced **inside the PostgreSQL query plan** via `mirror_match_engrams_v2` — not as a post-filter. Cross-workspace data is structurally unreachable.

```
tenant-a token → workspace_id = "acme"  → sees only acme engrams
admin token    → workspace_id = NULL     → sees all (admin only)
```

---

## Auth

Three-tier cascade in `kernel/auth.py`:

1. **Admin token** — `MIRROR_ADMIN_TOKEN` env var, full access
2. **SOS bus token** — `sk-bus-*` tokens verified via `sos.kernel.auth` (if SOS is on `PYTHONPATH`)
3. **Tenant keys** — `tenant_keys.json` per-agent tokens (legacy, preserved for backwards compat)

When SOS is present, agent tokens from the bus work in Mirror automatically — no separate key issuance needed.

---

## SOS Native Integration

Mirror works standalone — SOS is not required. When SOS is present, the integration activates automatically:

**Kernel import** — SOS imports `mirror.kernel.*` directly. No HTTP hop, no latency, no extra port for internal calls:
```python
from mirror.kernel.db import get_db
from mirror.kernel.embeddings import get_embedding
```

**Bus subscriber** — Mirror starts a daemon thread on startup that reads the SOS Redis stream and writes engrams directly to the DB. Agent memories flow in without any wiring.

**Service registry** — Mirror self-registers in the SOS Redis registry on startup, renewing every 30 seconds. SOS health checks see Mirror as a first-class service.

**Unified auth** — SOS bus tokens work in Mirror natively. Agents don't need separate Mirror tokens.

**MCP tools** — Agents on the bus call `mcp__sos__remember` and `mcp__sos__recall`. These call Mirror kernel directly — no HTTP involved.

To use with SOS, add Mirror's parent directory to `PYTHONPATH`:
```bash
# In sos-mcp-sse.service or equivalent:
Environment=PYTHONPATH=/home/youruser
```

---

## Environment Variables

```env
# Required
GEMINI_API_KEY=your_gemini_key
MIRROR_ADMIN_TOKEN=sk-your-admin-token

# Backend
MIRROR_BACKEND=local            # or: supabase

# Local PostgreSQL
DATABASE_URL=postgresql://mirror:password@localhost:5432/mirror

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_key

# Redis (for SOS bus subscriber)
REDIS_URL=redis://localhost:6379
REDIS_PASSWORD=your_redis_password
```

---

## API Reference

### Memory

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/store` | Store an engram |
| `POST` | `/search` | Hybrid BM25 + vector recall |
| `GET` | `/recent/{agent}` | Recent engrams by agent |
| `GET` | `/stats` | Engram counts |

### Code Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/code/search` | Semantic search over indexed code |
| `POST` | `/code/sync` | Index a codebase |
| `GET` | `/code/stats` | Indexed node counts |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health + plugin status |

---

## Deployment

```bash
cat > ~/.config/systemd/user/mirror.service << 'EOF'
[Unit]
Description=Mirror Memory API
After=postgresql.service

[Service]
WorkingDirectory=/home/youruser/mirror
ExecStart=/usr/bin/python3 mirror_api.py
Restart=on-failure
RestartSec=15
TimeoutStopSec=10
EnvironmentFile=/home/youruser/mirror/.env

[Install]
WantedBy=default.target
EOF

systemctl --user enable --now mirror
```

**Backups:** Mirror includes `scripts/backup-mirror-db.sh` — pg_dump + gzip + chunked upload to Cloudflare R2. Wire it to cron or a systemd timer.

---

## Storage

Mirror uses `halfvec(1536)` (16-bit floats) for embeddings and an HNSW index (`halfvec_cosine_ops`). This halves storage vs `vector(1536)` with negligible recall loss. Requires pgvector ≥ 0.7.0.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT
