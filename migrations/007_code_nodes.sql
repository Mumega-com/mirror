-- Mirror Code Nodes: structural graph nodes from all registered repos
-- Extends Mirror's pgvector to cover code as well as agent memories

CREATE TABLE IF NOT EXISTS mirror_code_nodes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id     TEXT NOT NULL,          -- original id from graph.db
    repo        TEXT NOT NULL,          -- repo root path (short name)
    repo_path   TEXT NOT NULL,          -- full path e.g. /mnt/.../torivers-staging-dev
    kind        TEXT NOT NULL,          -- function | class | module | method | etc.
    name        TEXT NOT NULL,
    qualified_name TEXT,
    file_path   TEXT NOT NULL,
    line_start  INT,
    line_end    INT,
    language    TEXT,
    signature   TEXT,                   -- what we embed
    embedding   vector(1536),
    synced_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (repo_path, node_id)
);

CREATE INDEX IF NOT EXISTS mirror_code_nodes_repo_idx
    ON mirror_code_nodes (repo);

CREATE INDEX IF NOT EXISTS mirror_code_nodes_kind_idx
    ON mirror_code_nodes (kind);

CREATE INDEX IF NOT EXISTS mirror_code_nodes_embedding_idx
    ON mirror_code_nodes
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- RPC: semantic search over code nodes
CREATE OR REPLACE FUNCTION mirror_match_code_nodes(
    query_embedding vector(1536),
    match_threshold float,
    match_count     int,
    filter_repo     text DEFAULT NULL,
    filter_kind     text DEFAULT NULL
)
RETURNS TABLE (
    id              UUID,
    node_id         TEXT,
    repo            TEXT,
    repo_path       TEXT,
    kind            TEXT,
    name            TEXT,
    qualified_name  TEXT,
    file_path       TEXT,
    line_start      INT,
    line_end        INT,
    language        TEXT,
    signature       TEXT,
    similarity      float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        n.id,
        n.node_id,
        n.repo,
        n.repo_path,
        n.kind,
        n.name,
        n.qualified_name,
        n.file_path,
        n.line_start,
        n.line_end,
        n.language,
        n.signature,
        1 - (n.embedding <=> query_embedding) AS similarity
    FROM mirror_code_nodes n
    WHERE
        1 - (n.embedding <=> query_embedding) > match_threshold
        AND (filter_repo IS NULL OR n.repo = filter_repo)
        AND (filter_kind IS NULL OR n.kind = filter_kind)
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;
