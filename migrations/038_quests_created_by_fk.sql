-- Migration 038: quests.created_by FK to principals(id)
-- Gate: G33 (F-19 — referential integrity)
-- Owner: Kasra
--
-- Pre-migration audit result (2026-04-25):
--   26 quests, all 26 have orphaned created_by values:
--   - 6 rows: created_by = 'test'  (E2E fixture artefact)
--   - 20 rows: created_by = quest's own id  (e.g. 'q:t1-global-01')
--             (fixture script put quest id in created_by by mistake)
--   No rows with valid principal references exist yet.
--
-- Backfill strategy: assign all orphaned rows to a new 'system' service
-- principal. This is the authoritative record for autopilot-created quests
-- until a brain/scheduler principal is minted. ON CONFLICT DO NOTHING so
-- re-running is idempotent.
--
-- ON DELETE RESTRICT: a principal cannot be hard-deleted while owning quests.
-- Soft-delete (nullify+confiscate) does not hit this constraint.
--
-- DEFERRABLE INITIALLY IMMEDIATE: allows within-transaction ordering of
-- principal + quest inserts (e.g. test fixtures) without disabling enforcement.
--
-- Q1 answer (from brief): brain does NOT emit quests today — quests are all
-- seed/fixture data. No agent:brain principal needed at this time.
-- Q2 answer: no Squad Service API endpoint validates created_by against
-- principals. The FK is the only enforcement point.

-- Step 1: ensure system principal exists
INSERT INTO principals (id, tenant_id, email, display_name, principal_type, status, mfa_required, created_at, updated_at)
VALUES (
    'system',
    'default',
    'system@sos.internal',
    'SOS System Principal',
    'service',
    'active',
    false,
    now(),
    now()
)
ON CONFLICT (id) DO NOTHING;

-- Step 2: backfill all orphaned created_by to 'system'
UPDATE quests
SET created_by = 'system'
WHERE created_by NOT IN (SELECT id FROM principals);

-- Step 3: verify clean before constraint add (will fail loudly if backfill missed any row)
DO $$
DECLARE
    orphan_count INTEGER;
BEGIN
    SELECT count(*) INTO orphan_count
    FROM quests
    WHERE created_by NOT IN (SELECT id FROM principals)
       OR created_by IS NULL;

    IF orphan_count > 0 THEN
        RAISE EXCEPTION 'migration 038: % orphaned created_by rows remain after backfill', orphan_count;
    END IF;
END $$;

-- Step 4: add FK constraint
ALTER TABLE quests
    ADD CONSTRAINT fk_quests_created_by
    FOREIGN KEY (created_by) REFERENCES principals(id)
    ON DELETE RESTRICT
    DEFERRABLE INITIALLY IMMEDIATE;
