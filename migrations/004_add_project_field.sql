-- Migration 004: Add project-separated memory
-- Task #4 from Athena Session 2
-- Adds project slug to engrams so memory can be scoped per-project

-- Add project column to mirror_engrams (Supabase)
ALTER TABLE mirror_engrams ADD COLUMN IF NOT EXISTS project TEXT;

-- Index for project filtering
CREATE INDEX IF NOT EXISTS idx_mirror_engrams_project ON mirror_engrams(project);

-- Composite index for agent + project queries
CREATE INDEX IF NOT EXISTS idx_mirror_engrams_agent_project ON mirror_engrams(series, project);

-- Update the match function to support project filtering
CREATE OR REPLACE FUNCTION mirror_match_engrams_v2(
  query_embedding vector(1536),
  match_threshold float,
  match_count int,
  filter_project text DEFAULT NULL
)
RETURNS TABLE (
  id uuid,
  context_id text,
  series text,
  project text,
  epistemic_truths text[],
  core_concepts text[],
  affective_vibe text,
  timestamp timestamptz,
  raw_data jsonb,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    me.id,
    me.context_id,
    me.series,
    me.project,
    me.epistemic_truths,
    me.core_concepts,
    me.affective_vibe,
    me.timestamp,
    me.raw_data,
    1 - (me.embedding <=> query_embedding) AS similarity
  FROM mirror_engrams me
  WHERE 1 - (me.embedding <=> query_embedding) > match_threshold
    AND (filter_project IS NULL OR me.project = filter_project)
  ORDER BY me.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
