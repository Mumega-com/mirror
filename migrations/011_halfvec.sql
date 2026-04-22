-- Migration 011: halfvec — 50% embedding storage reduction
-- Requires pgvector >= 0.7.0 (installed: 0.8.2)
-- Converts embedding column from vector(1536) to halfvec(1536).
-- Drops old ivfflat index, creates hnsw index (better recall + no train step).
-- Updates mirror_match_engrams_v2 (both overloads) to cast query vector → halfvec.

BEGIN;

-- 1. Drop old ivfflat index first (must happen before column type change)
DROP INDEX IF EXISTS mirror_engrams_embedding_idx;

-- 2. Convert column type
ALTER TABLE mirror_engrams
  ALTER COLUMN embedding TYPE halfvec(1536)
  USING embedding::halfvec(1536);

CREATE INDEX mirror_engrams_embedding_idx
  ON mirror_engrams
  USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 3. Update function — 4-param overload (legacy, no workspace isolation)
CREATE OR REPLACE FUNCTION mirror_match_engrams_v2(
  query_embedding vector,
  match_threshold double precision,
  match_count     integer,
  filter_project  text DEFAULT NULL
)
RETURNS TABLE (
  id               uuid,
  context_id       text,
  series           text,
  project          text,
  epistemic_truths text[],
  core_concepts    text[],
  affective_vibe   text,
  energy_level     text,
  next_attractor   text,
  raw_data         jsonb,
  ts               timestamptz,
  similarity       double precision
)
LANGUAGE plpgsql AS $$
DECLARE
  hv halfvec(1536) := query_embedding::halfvec(1536);
BEGIN
  RETURN QUERY
  SELECT
    me.id, me.context_id, me.series, me.project,
    me.epistemic_truths, me.core_concepts, me.affective_vibe,
    me.energy_level, me.next_attractor, me.raw_data,
    me.timestamp AS ts,
    1 - (me.embedding <=> hv) AS similarity
  FROM mirror_engrams me
  WHERE
    1 - (me.embedding <=> hv) > match_threshold
    AND (filter_project IS NULL OR me.project = filter_project)
  ORDER BY me.embedding <=> hv
  LIMIT match_count;
END;
$$;

-- 4. Update function — 5-param overload (with workspace_id isolation)
CREATE OR REPLACE FUNCTION mirror_match_engrams_v2(
  query_embedding    vector,
  match_threshold    double precision,
  match_count        integer,
  filter_project     text DEFAULT NULL,
  filter_workspace_id text DEFAULT NULL
)
RETURNS TABLE (
  id               uuid,
  context_id       text,
  series           text,
  project          text,
  workspace_id     text,
  epistemic_truths text[],
  core_concepts    text[],
  affective_vibe   text,
  energy_level     text,
  next_attractor   text,
  raw_data         jsonb,
  ts               timestamptz,
  similarity       double precision
)
LANGUAGE plpgsql AS $$
DECLARE
  hv halfvec(1536) := query_embedding::halfvec(1536);
BEGIN
  RETURN QUERY
  SELECT
    me.id, me.context_id, me.series, me.project, me.workspace_id,
    me.epistemic_truths, me.core_concepts, me.affective_vibe,
    me.energy_level, me.next_attractor, me.raw_data,
    me.timestamp AS ts,
    1 - (me.embedding <=> hv) AS similarity
  FROM mirror_engrams me
  WHERE
    1 - (me.embedding <=> hv) > match_threshold
    AND (filter_project IS NULL OR me.project = filter_project)
    AND (filter_workspace_id IS NULL OR me.workspace_id = filter_workspace_id)
  ORDER BY me.embedding <=> hv
  LIMIT match_count;
END;
$$;

COMMIT;
