-- Migration 008: Enforce workspace_id isolation in mirror_match_engrams_v2
-- Adds filter_workspace_id parameter to the function and workspace_id to its
-- return type so the DB enforces tenant isolation inside the query plan.

CREATE OR REPLACE FUNCTION mirror_match_engrams_v2(
  query_embedding vector(1536),
  match_threshold float,
  match_count int,
  filter_project text DEFAULT NULL,
  filter_workspace_id text DEFAULT NULL
)
RETURNS TABLE (
  id uuid,
  context_id text,
  series text,
  project text,
  workspace_id text,
  epistemic_truths text[],
  core_concepts text[],
  affective_vibe text,
  energy_level text,
  next_attractor text,
  raw_data jsonb,
  ts timestamptz,
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
    me.workspace_id,
    me.epistemic_truths,
    me.core_concepts,
    me.affective_vibe,
    me.energy_level,
    me.next_attractor,
    me.raw_data,
    me.timestamp AS ts,
    1 - (me.embedding <=> query_embedding) AS similarity
  FROM mirror_engrams me
  WHERE
    1 - (me.embedding <=> query_embedding) > match_threshold
    AND (filter_project IS NULL OR me.project = filter_project)
    AND (filter_workspace_id IS NULL OR me.workspace_id = filter_workspace_id)
  ORDER BY me.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
