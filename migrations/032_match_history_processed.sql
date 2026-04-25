-- Migration 032: §16 A.6 — Learning Loop — match_history processing sentinel
-- Gate: Athena G17 (post A.6 contract ship)
-- Owner: Kasra
--
-- Adds reputation_processed_at to match_history so the learning loop can
-- idempotently track which outcomes have been fed into Glicko-2.
--
-- Run as: psql -U mirror -d mirror -f 032_match_history_processed.sql

ALTER TABLE match_history
    ADD COLUMN IF NOT EXISTS reputation_processed_at TIMESTAMPTZ;

-- Fast lookup for the learning loop: pending outcomes only
CREATE INDEX IF NOT EXISTS idx_match_history_pending_outcomes
    ON match_history (outcome_at ASC)
    WHERE outcome IS NOT NULL AND reputation_processed_at IS NULL;

COMMENT ON COLUMN match_history.reputation_processed_at IS
    '§16 A.6: timestamp when this outcome was processed by the learning loop '
    '(Glicko-2 event emitted + citizen_vectors nudged). NULL = not yet processed.';
