-- Migration 009: Add tsvector column for hybrid (vector + full-text) search
-- Enables GIN-indexed full-text search on engram text content alongside pgvector ANN.

ALTER TABLE mirror_engrams ADD COLUMN IF NOT EXISTS text_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(raw_data->>'text', ''))) STORED;

CREATE INDEX IF NOT EXISTS mirror_engrams_tsv_idx ON mirror_engrams USING GIN(text_tsv);
