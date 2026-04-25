-- Migration 045: stripe_webhook_processed + contracts hardening — Sprint 006 E.3 / G69 v0.3
-- Gate: Kasra (Athena G69 GREEN 2026-04-25 23:27 UTC)
--
-- stripe_webhook_processed: idempotency + audit table for Stripe payment_intent.succeeded.
--   id TEXT PK: server-minted UUID; used as FK proof-of-authorization in mint_knight_programmatic.
--   payment_intent_id UNIQUE: one row per payment (concurrent retries serialised at INSERT).
--   status: processing | processed | failed state machine (WARN-2).
--     processing: INSERT on first receipt; row is in-flight.
--     processed:  UPDATE on successful mint commit. 200 replays return prior knight.
--     failed:     UPDATE on permanent failure (no_contract, project_scope_refused).
--                 Transient failures (mint error) rollback the transaction → row disappears → retry-safe.
--   APPEND-ONLY (Athena WARN-6): no DELETE path. Pruning via archival-export only.
--
-- contracts: adds knight_id + cause_statement + project for E.3 knight linkage.
--   knight_id:       populated by E.3 handler after successful mint.
--   cause_statement: customer-supplied cause declaration.
--   project:         authoritative project scope (BLOCK-2 — source-of-truth, not pi.metadata.project).
--
-- BLOCK-4: UNIQUE partial index on contracts prevents duplicate knight per customer+project.

CREATE TABLE IF NOT EXISTS stripe_webhook_processed (
    id                        TEXT        PRIMARY KEY,          -- server-minted UUID (BLOCK-5 FK anchor)
    payment_intent_id         TEXT        NOT NULL UNIQUE,
    status                    TEXT        NOT NULL DEFAULT 'processing'
                                          CHECK (status IN ('processing', 'processed', 'failed')),
    processed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at              TIMESTAMPTZ,
    resulting_knight_id       TEXT,
    resulting_knight_qnft_uri TEXT,
    last_error                TEXT
);

COMMENT ON TABLE stripe_webhook_processed IS
    'Idempotency + audit table for Stripe payment_intent.succeeded webhooks. '
    'APPEND-ONLY (WARN-6): no DELETE path — failed rows stay for audit. '
    'id is server-minted UUID used as FK proof-of-authorization in mint_knight_programmatic (BLOCK-5). '
    'Sprint 006 E.3/G69 v0.3.';

-- Add knight linkage columns to contracts (idempotent)
ALTER TABLE contracts
    ADD COLUMN IF NOT EXISTS knight_id          TEXT,
    ADD COLUMN IF NOT EXISTS cause_statement    TEXT,
    ADD COLUMN IF NOT EXISTS project            TEXT DEFAULT 'mumega';

COMMENT ON COLUMN contracts.knight_id IS
    'Knight agent ID minted for this customer on payment success (E.3/G69).';
COMMENT ON COLUMN contracts.cause_statement IS
    'Customer-supplied cause declaration used as QNFT identity seed (E.3/G69).';
COMMENT ON COLUMN contracts.project IS
    'Authoritative project scope (BLOCK-2): source-of-truth for knight routing. '
    'NOT webhook metadata — metadata is attacker-writable. Default: mumega (V1 scope).';

-- BLOCK-4: exactly one knight per customer per project
-- Partial index: only enforced once a knight has been minted (knight_id IS NOT NULL).
CREATE UNIQUE INDEX IF NOT EXISTS idx_contracts_one_knight_per_customer_project
    ON contracts (stripe_customer_id, project)
    WHERE knight_id IS NOT NULL;

COMMENT ON INDEX idx_contracts_one_knight_per_customer_project IS
    'Exactly-one-knight-per-customer-per-project (BLOCK-4). '
    'Multi-knight-per-customer intentionally out of scope V1 per §3 brief. '
    'Sprint 006 E.3/G69 v0.3.';
