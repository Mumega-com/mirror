# Mirror

**Shared semantic memory for AI agent teams.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Status: Active](https://img.shields.io/badge/status-active-brightgreen.svg)]()

Mirror is a FastAPI service that gives your AI agents a shared long-term memory. Agents store "engrams" (episodic memories as text) and retrieve them later by meaning, not keyword. It also indexes codebases so agents can search functions and classes by description.

Runs on a $5 VPS. Free to operate (uses Gemini embedding API free tier).

---

## Why Mirror?

| | Mirror | Mem0 | Letta | Zep |
|---|---|---|---|---|
| Shared across all agents | Yes | No (per-user) | No (per-agent) | Partial |
| Self-hosted | Yes | SaaS | Self-hosted | Hosted |
| Code graph search | Yes | No | No | No |
| Free to run | Yes | Paid tiers | Compute cost | Hosted only |
| MCP-native | Yes | No | No | No |

The key difference: Mirror is a **team memory**, not a per-agent memory. When agent A stores something, agent B can find it. Most memory systems scope memory per-user or per-agent. Mirror scopes by project.

---

## Architecture

```
Agents (Claude, GPT, Gemini, custom)
         |
         | HTTP / Bearer token
         v
    Mirror API (:8844)
    ┌────────────────────────────────────┐
    │  POST /store     → store engram    │
    │  POST /search    → semantic recall │
    │  GET  /recent    → latest by agent │
    │  POST /code/search → code search   │
    │  POST /code/sync  → index a repo  │
    └────────────────────────────────────┘
         |
         | psycopg2 / supabase-py
         v
    PostgreSQL + pgvector
    ┌────────────────────────────────────┐
    │  mirror_engrams      (memories)    │
    │  mirror_code_nodes   (code graph)  │
    └────────────────────────────────────┘
         |
    Embeddings via Gemini embedding-001
    (free tier, 1536-dim, truncated)
```

**Two backends:**
- `MIRROR_BACKEND=local` — plain PostgreSQL via psycopg2 (default)
- `MIRROR_BACKEND=supabase` — Supabase client (for hosted deployments)

Same API, same schema, swap with one env var.

---

## Quick Start

**Requirements:** Python 3.11+, PostgreSQL with pgvector, Gemini API key (free at [aistudio.google.com](https://aistudio.google.com))

```bash
# 1. Clone and install deps
git clone https://github.com/Mumega-com/mirror.git
cd mirror
pip install fastapi uvicorn psycopg2-binary python-dotenv google-genai pydantic

# 2. Set up PostgreSQL with pgvector
#    On Ubuntu: sudo apt install postgresql postgresql-contrib
#    pgvector: https://github.com/pgvector/pgvector#installation
createdb mirror
psql mirror < schema.sql

# 3. Configure environment
cp .env.example .env
# Edit .env — set GEMINI_API_KEY and DATABASE_URL

# 4. Run
python mirror_api.py
# API is live at http://localhost:8844
```

**Store a memory:**
```bash
curl -X POST http://localhost:8844/store \
  -H "Authorization: Bearer sk-mumega-internal-001" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "kasra",
    "context_id": "session_20260412_001",
    "text": "The user prefers Python over TypeScript for backend services.",
    "core_concepts": ["python", "backend", "preferences"]
  }'
```

**Search memories:**
```bash
curl -X POST http://localhost:8844/search \
  -H "Authorization: Bearer sk-mumega-internal-001" \
  -H "Content-Type: application/json" \
  -d '{"query": "language preferences", "top_k": 3}'
```

---

## Environment Variables

Create a `.env` file in the mirror directory:

```env
# Required
GEMINI_API_KEY=your_gemini_key_here
MIRROR_ADMIN_TOKEN=sk-your-secret-token

# Backend selection (default: local)
MIRROR_BACKEND=local

# Local PostgreSQL (used when MIRROR_BACKEND=local)
DATABASE_URL=postgresql://mirror:password@localhost:5432/mirror

# Supabase (used when MIRROR_BACKEND=supabase)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
```

---

## API Endpoints

### Memory

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/store` | Store an engram (text + metadata → embedding) |
| `POST` | `/search` | Semantic search by natural language query |
| `GET` | `/recent/{agent}` | Recent engrams from a specific agent |
| `GET` | `/stats` | Engram counts by agent |
| `POST` | `/extract` | Auto-extract memories from conversation text |
| `POST` | `/smart_search` | Decay-aware search (boosts recently accessed) |
| `POST` | `/consolidate` | Merge near-duplicate memories |

### Code Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/code/search` | Semantic search over indexed code |
| `POST` | `/code/sync` | Index a codebase (triggers background job) |
| `GET` | `/code/stats` | Count of indexed nodes per repo |

### Misc

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/art/{type}` | Generate SVG art (noise, sacred, mandala, spiral) |

---

## Code Search

Mirror can index your codebase and let agents search functions and classes by description — not filename.

```bash
# Sync a repo into Mirror
curl -X POST "http://localhost:8844/code/sync?repo=my-project"

# Search by description
curl -X POST http://localhost:8844/code/search \
  -H "Authorization: Bearer sk-mumega-internal-001" \
  -H "Content-Type: application/json" \
  -d '{"query": "function that handles user authentication", "top_k": 5}'
```

Response includes file path, line numbers, function signature, and similarity score. Works across multiple repos. Filter by repo or node kind (function, class, method).

This is built on top of [code-review-graph](https://github.com/servathadi/mumega-docs), which parses your code with Tree-sitter and maintains a structural graph. Mirror adds semantic embeddings on top of that graph.

---

## Auth

Mirror uses Bearer token auth. Set `MIRROR_ADMIN_TOKEN` in `.env` for full access.

For multi-agent setups, you can issue per-agent tokens in `tenant_keys.json`. Tenant tokens are scoped — an agent can only read and write its own namespace.

```json
[
  {
    "key": "sk-agent-kasra-abc123",
    "agent_slug": "kasra",
    "active": true
  }
]
```

---

## Works with SOS

Mirror is the memory layer for the [SOS agent bus](https://github.com/servathadi/mumega-docs/tree/main/architecture). Agents on the bus call `mcp__sos__remember` and `mcp__sos__recall` — those tools proxy to Mirror's `/store` and `/search` endpoints.

You don't need SOS to use Mirror. Mirror is a standalone HTTP service. But if you're running a multi-agent system and want structured coordination on top of the memory, SOS handles that.

---

## Deployment

Mirror runs as a systemd user service on a Hetzner VPS. The GitHub Actions workflow in `.github/workflows/ci-deploy.yml` lints on every push and deploys to the VPS on merge to main.

For your own deployment:

```bash
# Install as a systemd service
cat > ~/.config/systemd/user/mirror.service << 'EOF'
[Unit]
Description=Mirror Memory API
After=postgresql.service

[Service]
WorkingDirectory=/home/youruser/mirror
ExecStart=/usr/bin/python3 mirror_api.py
Restart=always
EnvironmentFile=/home/youruser/mirror/.env

[Install]
WantedBy=default.target
EOF

systemctl --user enable mirror
systemctl --user start mirror
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT
