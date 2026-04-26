-- Migration 048: add 'alerted' to gtm.actions status CHECK — Sprint 008 G80 BLOCK-E-1
-- Gate: Kasra
--
-- knight_protocols.mark_action_alerted() transitions status to 'alerted'
-- but migration 047 CHECK constraint only allows: pending, done, skipped, failed.

ALTER TABLE gtm.actions DROP CONSTRAINT IF EXISTS actions_status_check;
ALTER TABLE gtm.actions ADD CONSTRAINT actions_status_check
  CHECK (status IN ('pending', 'done', 'skipped', 'failed', 'alerted'));
