# Mirror

Self-hosted multi-tenant agent memory with Postgres RLS and MCP.

Mirror stores agent memories as engrams, retrieves them with vector search, and
uses workspace-scoped tokens so tenants cannot read each other's data. It runs
standalone by default; SOS bus integration is optional.

## Quick Start

Requirements:

- Python 3.11+
- PostgreSQL 14+ with pgvector
- An embedding provider key, unless `MIRROR_EMBED_PROVIDER=local`

```bash
git clone https://github.com/mumega-com/mirror.git
cd mirror
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
psql "$DATABASE_URL" < schema.sql
python mirror_api.py
```

Mirror listens on `http://localhost:8844` by default.

## Configuration

```env
DATABASE_URL=<your-postgres-dsn>
MIRROR_ADMIN_TOKEN=sk-change-me
MIRROR_API_KEY=sk-workspace-token
MIRROR_EMBED_PROVIDER=openai
OPENAI_API_KEY=sk-your-openai-key
```

Embedding providers:

- `openai` uses `text-embedding-3-small` by default.
- `local-onnx` uses `fastembed`.
- `local` uses a deterministic hash embedding for tests and offline demos.

## REST API

Store a memory:

```bash
curl -X POST http://localhost:8844/store \
  -H "Authorization: Bearer $MIRROR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "planner",
    "context_id": "session-001",
    "text": "The user prefers Python for backend services.",
    "core_concepts": ["python", "backend"]
  }'
```

Recall memories:

```bash
curl -X POST http://localhost:8844/search \
  -H "Authorization: Bearer $MIRROR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "backend language preference", "top_k": 5}'
```

List recent memories:

```bash
curl http://localhost:8844/recent/planner?limit=10 \
  -H "Authorization: Bearer $MIRROR_API_KEY"
```

## MCP

The stdio server exposes:

- `remember(content, agent_id, metadata?)`
- `recall(query, agent_id, limit?)`
- `recent(agent_id, limit?)`

Example client config:

```json
{
  "mcpServers": {
    "mirror": {
      "command": "python",
      "args": ["/path/to/mirror/mirror_mcp_stdio.py"],
      "env": {
        "MIRROR_URL": "http://localhost:8844",
        "MIRROR_API_KEY": "sk-workspace-token"
      }
    }
  }
}
```

## Admin

Create a workspace and issue a scoped token:

```bash
curl -X POST http://localhost:8844/admin/workspaces \
  -H "Authorization: Bearer $MIRROR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"slug": "acme", "name": "Acme"}'

curl -X POST http://localhost:8844/admin/workspaces/<workspace-id>/tokens \
  -H "Authorization: Bearer $MIRROR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label": "planner", "token_type": "agent", "owner_id": "planner"}'
```

Only token hashes are stored. The plaintext token is returned once.

## SOS Integration

Mirror does not require SOS. If you run SOS, set `SOS_BUS_URL` to enable
bus-subscriber mode and provide an adapter module with
`MIRROR_SOS_SUBSCRIBER_MODULE`. Without `SOS_BUS_URL`, Mirror runs standalone.

The OSS repository does not ship SOS-specific code.

## Tests

Run the standalone test suite:

```bash
pip install -r requirements.txt pytest
pytest
```

## Safety

Never commit:

- `.env`
- `tenant_keys.json`
- `data/`
- agent-local memory folders under `agents/`
