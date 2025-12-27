
-- SCHEMA FOR PROJECT MIRROR: COGNITIVE ENGRAM STORAGE
-- DATABASE: PostgreSQL with pgvector extension

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create Engrams Table
CREATE TABLE IF NOT EXISTS mirror_engrams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id TEXT NOT NULL UNIQUE,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    series TEXT,
    
    -- Metadata (Searchable)
    epistemic_truths TEXT[],
    core_concepts TEXT[],
    affective_vibe TEXT,
    energy_level TEXT,
    next_attractor TEXT,
    
    -- RAW Engram JSON
    raw_data JSONB NOT NULL,
    
    -- Vector Embeddings (for Semantic Recall)
    -- Using 1536 dimensions (matching OpenAI text-embedding-3-small or equivalent)
    embedding vector(1536)
);

-- 3. Create Vector Index for Cosine Similarity
CREATE INDEX ON mirror_engrams USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 4. Audit Log (Tracking State Drift)
CREATE TABLE IF NOT EXISTS mirror_state_audit_log (
    id SERIAL PRIMARY KEY,
    engram_id UUID REFERENCES mirror_engrams(id),
    change_type TEXT,
    old_value JSONB,
    new_value JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Create a function to search for engrams by vector similarity
CREATE OR REPLACE FUNCTION mirror_match_engrams (
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
RETURNS TABLE (
  id UUID,
  context_id TEXT,
  series TEXT,
  epistemic_truths TEXT[],
  core_concepts TEXT[],
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    mirror_engrams.id,
    mirror_engrams.context_id,
    mirror_engrams.series,
    mirror_engrams.epistemic_truths,
    mirror_engrams.core_concepts,
    1 - (mirror_engrams.embedding <=> query_embedding) AS similarity
  FROM mirror_engrams
  WHERE 1 - (mirror_engrams.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;
