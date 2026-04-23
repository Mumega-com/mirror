-- DS-001: Tag all existing engrams from internal agents as mumega-internal workspace.
-- Engrams written by internal SOS agents (kasra, athena, loom, etc.) previously
-- landed with workspace_id=NULL (admin pool). This migration moves them into the
-- explicit "mumega-internal" namespace so they are isolated from customer data.
--
-- Safe to re-run — WHERE clause is idempotent.

BEGIN;

UPDATE mirror_engrams
SET workspace_id = 'mumega-internal'
WHERE workspace_id IS NULL
  AND (
    project IN ('sos')
    OR raw_data->>'agent' IN (
      'kasra','athena','loom','sovereign','mumega','codex',
      'sol','hermes','river','worker','dandan','mkt-lead',
      'mizan','gemma','dara','sos-medic','agentlink'
    )
  );

COMMIT;
