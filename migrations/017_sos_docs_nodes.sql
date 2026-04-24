-- Migration 017: sos-docs service tables (doc-node graph)
-- Phase 4 / Burst 2A — §12 sos-docs
-- Owner: Athena (schema) · Kasra (service, ingest)
-- Tier enforcement: delegated to §1C recall SQL in sos/contracts/tiers.py — never re-implemented here.

CREATE TABLE IF NOT EXISTS docs_nodes (
    id               TEXT PRIMARY KEY,          -- stable slug, e.g. 'sos/stack-sections/12-sos-docs'
    tier             TEXT NOT NULL DEFAULT 'project'
                         CHECK (tier IN ('public','squad','project','role','entity','private')),
    entity_id        TEXT,                      -- workspace/customer when tier='entity'
    permitted_roles  TEXT[],                    -- when tier='role'
    project_id       TEXT,
    squad_id         TEXT,
    author_id        TEXT NOT NULL,
    title            TEXT NOT NULL,
    summary          TEXT,
    body             TEXT NOT NULL,             -- markdown source
    body_format      TEXT NOT NULL DEFAULT 'markdown'
                         CHECK (body_format IN ('markdown','mdx','plaintext')),
    frontmatter      JSONB,                     -- Astro-compatible metadata
    version          TEXT NOT NULL DEFAULT '1.0',
    supersedes       TEXT REFERENCES docs_nodes(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_docs_nodes_tier
    ON docs_nodes (tier);

CREATE INDEX IF NOT EXISTS idx_docs_nodes_entity_id
    ON docs_nodes (entity_id);

CREATE INDEX IF NOT EXISTS idx_docs_nodes_permitted_roles
    ON docs_nodes USING GIN (permitted_roles);

CREATE INDEX IF NOT EXISTS idx_docs_nodes_project
    ON docs_nodes (project_id);

CREATE INDEX IF NOT EXISTS idx_docs_nodes_squad
    ON docs_nodes (squad_id);

-- Auto-update updated_at on every mutation (G2 note: spec omitted this trigger)
CREATE OR REPLACE FUNCTION docs_nodes_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_docs_nodes_updated_at ON docs_nodes;
CREATE TRIGGER trg_docs_nodes_updated_at
    BEFORE UPDATE ON docs_nodes
    FOR EACH ROW EXECUTE FUNCTION docs_nodes_set_updated_at();


-- Doc-node relations (explicit edge graph)
CREATE TABLE IF NOT EXISTS docs_relations (
    id          BIGSERIAL PRIMARY KEY,
    from_node   TEXT NOT NULL REFERENCES docs_nodes(id) ON DELETE CASCADE,
    to_node     TEXT NOT NULL REFERENCES docs_nodes(id) ON DELETE CASCADE,
    edge_type   TEXT NOT NULL
                    CHECK (edge_type IN (
                        'articulates',
                        'derives_from',
                        'sequences',
                        'specced_in',
                        'supersedes',
                        'exemplifies'
                    )),
    weight      NUMERIC,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_node, to_node, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_docs_relations_from
    ON docs_relations (from_node, edge_type);

CREATE INDEX IF NOT EXISTS idx_docs_relations_to
    ON docs_relations (to_node, edge_type);
