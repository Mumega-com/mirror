-- Migration 052: mirror_pending_receipts (Mirror outbox table)
-- S024 Track F Phase 2 LOCK F-16
--
-- Closes the silent-fail-open canon target documented in
-- /home/mumega/mirror/kernel/receipts.py L67-68 (InkwellReceiptClient.append
-- swallows every exception and returns None) by giving the caller a durable
-- staging row written in the SAME transaction as the underlying business
-- write (`mirror_engrams` upsert). A separate drain worker reads pending
-- rows and POSTs them to the receipts endpoint with retry/backoff, so a
-- network blip / Inkwell-down window no longer drops audit signal.
--
-- LOCK-OUTBOX-1: state CHECK constraint enforces the only three legal
--                phases; partial indexes match the two hot-path queries
--                (claimable, DLQ-inspect).
-- LOCK-OUTBOX-2: SKIP LOCKED claim pattern (see kernel/outbox.py) requires
--                visible_after as a query column for backoff scheduling.
-- LOCK-OUTBOX-3: payload is opaque JSONB — schema versioning lives inside
--                payload (`payload->>'_v'`) rather than as a column so
--                future receipt shapes don't require a migration.

CREATE TABLE IF NOT EXISTS mirror_pending_receipts (
    id              BIGSERIAL PRIMARY KEY,
    queue_name      TEXT        NOT NULL DEFAULT 'inkwell-receipts',
    payload         JSONB       NOT NULL,
    state           TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'in_flight', 'dlq')),
    attempt_count   INTEGER     NOT NULL DEFAULT 0,
    max_attempts    INTEGER     NOT NULL DEFAULT 8,
    visible_after   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- LOCK-OUTBOX-1: drain worker hot path — claim next visible pending row
CREATE INDEX IF NOT EXISTS idx_mirror_pending_receipts_claimable
    ON mirror_pending_receipts (queue_name, visible_after, id)
    WHERE state = 'pending';

-- LOCK-OUTBOX-1: F-17 substrate-monitor queries DLQ count by queue
CREATE INDEX IF NOT EXISTS idx_mirror_pending_receipts_dlq
    ON mirror_pending_receipts (queue_name, updated_at DESC)
    WHERE state = 'dlq';

-- Optional: surface in-flight rows for a stuck-claim sweeper. Cheap because
-- in_flight is rare relative to pending. Not yet consumed but keeps the
-- forensic surface ready for the F-17 sweep tool if drain crashes mid-claim.
CREATE INDEX IF NOT EXISTS idx_mirror_pending_receipts_in_flight
    ON mirror_pending_receipts (queue_name, updated_at)
    WHERE state = 'in_flight';
