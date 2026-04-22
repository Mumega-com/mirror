-- Migration 010: Add owner_type and owner_id to mirror_engrams
-- These columns are set by the store route from the token context but were
-- never added to the schema, causing all /store calls to fail.

ALTER TABLE mirror_engrams ADD COLUMN IF NOT EXISTS owner_type TEXT;
ALTER TABLE mirror_engrams ADD COLUMN IF NOT EXISTS owner_id TEXT;
