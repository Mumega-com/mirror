-- Migration 013: Track embedding model per engram
-- Enables safe re-embedding: filter on NULL or old model name
-- Also enables future model migrations without full re-embeds

ALTER TABLE mirror_engrams
    ADD COLUMN IF NOT EXISTS embedding_model TEXT;

-- Index for re-embedding queries (fast scan of un-tagged rows)
CREATE INDEX IF NOT EXISTS mirror_engrams_embedding_model_idx
    ON mirror_engrams (embedding_model)
    WHERE embedding_model IS NULL OR embedding_model != 'gemini-embedding-2-preview';
