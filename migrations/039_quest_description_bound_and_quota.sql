-- Migration 039: quest description length bound + extraction quota table
-- Gate: G29 (F-13 — cost amplification via oversized/mass quest descriptions)
-- Owner: Kasra
--
-- Pre-migration state (2026-04-25):
--   quests.description is TEXT with no length bound.
--   No per-creator extraction quota table exists.
--
-- Part A: CHECK constraint on quests.description
--   Cap at 4096 characters. Existing rows: all have descriptions under 4096
--   (pre-audit confirmed — longest observed ~200 chars). Safe to add without
--   backfill. If any row exceeds 4096, ALTER TABLE fails loudly — fix data first.
--
-- Part B: quest_extraction_quota table
--   Tracks per-creator, per-day Vertex extraction call counts.
--   FK to principals ON DELETE CASCADE: quota rows auto-delete when creator is
--   removed (soft-delete + nullify doesn't fire FK, but hard-delete does).
--   Daily window_date: UTC date (current_date in Postgres is UTC-aligned when
--   TIMEZONE='UTC' on the server). Cleanup job removes rows older than 7 days.
--
-- EXTRACTION_QUOTA_DAILY default: 10 per creator per day.
-- Configurable via env var EXTRACTION_QUOTA_DAILY in the application layer.

-- Part A: description length constraint
ALTER TABLE quests
    ADD CONSTRAINT chk_quests_description_length
    CHECK (length(description) <= 4096);

-- Part B: extraction quota table
CREATE TABLE IF NOT EXISTS quest_extraction_quota (
    creator_id   TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    window_date  DATE NOT NULL,
    used_count   INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (creator_id, window_date)
);

-- Index for daily cleanup job
CREATE INDEX IF NOT EXISTS idx_extraction_quota_window_date
    ON quest_extraction_quota (window_date);
