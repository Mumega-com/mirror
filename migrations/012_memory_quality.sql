-- Migration 012: Memory quality columns
-- Adds: reference_count, memory_tier, importance_score, archived
-- Required by: online dedup (Phase 1), Dreamer agent (Phase 2)

ALTER TABLE mirror_engrams
    ADD COLUMN IF NOT EXISTS reference_count  INT     NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS memory_tier      TEXT    NOT NULL DEFAULT 'working',
    ADD COLUMN IF NOT EXISTS importance_score FLOAT   NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS archived         BOOLEAN NOT NULL DEFAULT false;

-- Index for Dreamer queries: find unarchived engrams by tier + importance
CREATE INDEX IF NOT EXISTS mirror_engrams_tier_idx
    ON mirror_engrams (memory_tier, importance_score DESC)
    WHERE archived = false;

-- Index for dedup queries: quick lookup of recent engrams by workspace
CREATE INDEX IF NOT EXISTS mirror_engrams_workspace_recent_idx
    ON mirror_engrams (workspace_id, timestamp DESC)
    WHERE archived = false;
