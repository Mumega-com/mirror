-- Mirror Cloudflare (D1) schema — SQLite dialect (D1 is SQLite).
-- Mirrors mirror_engrams / mirror_code_nodes from kernel/db_sqlite.py.
-- Vectors live in Cloudflare Vectorize, NOT in D1.
--
-- Apply once before first use:
--   npx wrangler d1 execute <DB_NAME> --remote --file kernel/db_cloudflare_schema.sql
--
-- Create the matching Vectorize indexes (dims must equal MIRROR_VECTOR_DIMS):
--   npx wrangler vectorize create mirror-engrams       --dimensions=1536 --metric=cosine
--   npx wrangler vectorize create mirror-engrams-code  --dimensions=1536 --metric=cosine
--
-- Metadata indexes so $eq filters work (tenant isolation is load-bearing):
--   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=workspace_id --type=string
--   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=project      --type=string
--   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=owner_type   --type=string
--   npx wrangler vectorize create-metadata-index mirror-engrams --property-name=owner_id     --type=string

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
