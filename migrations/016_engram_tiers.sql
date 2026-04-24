-- 016: Add five-tier access model to engrams.
--
-- Adds tier, entity_id, and permitted_roles columns.
-- Backfills all existing engrams to tier='project' with entity_id=workspace_id.
-- Safe to re-run (ALTER TABLE with existing columns will fail silently via IF NOT EXISTS workaround).

BEGIN;

ALTER TABLE mirror_engrams ADD COLUMN tier TEXT NOT NULL DEFAULT 'project';
ALTER TABLE mirror_engrams ADD COLUMN entity_id TEXT;
ALTER TABLE mirror_engrams ADD COLUMN permitted_roles TEXT[];

CREATE INDEX idx_engrams_tier            ON mirror_engrams (tier);
CREATE INDEX idx_engrams_entity_id       ON mirror_engrams (entity_id);
CREATE INDEX idx_engrams_permitted_roles ON mirror_engrams USING GIN (permitted_roles);

-- Backfill: all existing engrams stay at project scope, entity_id = workspace_id
UPDATE mirror_engrams
SET entity_id = workspace_id
WHERE entity_id IS NULL;

-- Add check constraint for valid tiers
ALTER TABLE mirror_engrams ADD CONSTRAINT engrams_tier_check
  CHECK (tier IN ('public', 'squad', 'project', 'entity', 'private'));

COMMIT;
