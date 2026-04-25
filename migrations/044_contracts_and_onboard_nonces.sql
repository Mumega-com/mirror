-- Migration 044: contracts + onboard_nonces — Sprint 006 E.2 / G68
-- Gate: Kasra
--
-- contracts: stores signed agreement artifacts linking a principal to a Stripe Quote.
--   - One row per pending/accepted/voided contract.
--   - stripe_quote_id UNIQUE enforces idempotency (Stripe webhook replays are safe).
--   - signed_at is set by the E.3 stripe-webhook handler when payment_intent.succeeded fires.
--
-- onboard_nonces: server-side state store for GitHub OAuth flows.
--   - nonce TEXT PK: cryptographically random (32 bytes hex), sent as OAuth state= parameter.
--   - intent JSONB: arbitrary metadata from the initiating side (source channel, prospect email
--     hint, plan preference, etc.). Validated HMAC on issue; nonce itself is the bearer credential.
--   - expires_at: 10-minute TTL — standard GitHub OAuth window.
--   - consumed_at: set when the callback fires; NULL = pending. Consumed nonces are rejected.
--
-- Cleanup: onboard_nonces rows older than 1 hour can be deleted by any periodic job (e.g.
-- mfa_cleanup.timer); add a WHERE expires_at < now() - interval '1 hour' predicate.

CREATE TABLE IF NOT EXISTS contracts (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id       UUID        REFERENCES principals(id) ON DELETE RESTRICT,
    tenant_slug        TEXT,                                     -- populated after tenant provision (E.3)
    stripe_customer_id TEXT,
    stripe_quote_id    TEXT        UNIQUE NOT NULL,              -- idempotency key for Stripe replays
    stripe_quote_url   TEXT,
    status             TEXT        NOT NULL DEFAULT 'draft'
                                   CHECK (status IN ('draft', 'sent', 'accepted', 'void')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    signed_at          TIMESTAMPTZ                               -- set by E.3 Stripe webhook handler
);

CREATE INDEX IF NOT EXISTS idx_contracts_principal_id
    ON contracts (principal_id);

CREATE INDEX IF NOT EXISTS idx_contracts_status
    ON contracts (status, created_at DESC);

-- onboard_nonces: transient state for in-progress GitHub OAuth flows
CREATE TABLE IF NOT EXISTS onboard_nonces (
    nonce        TEXT        PRIMARY KEY,
    intent       JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '10 minutes',
    consumed_at  TIMESTAMPTZ                                     -- NULL = pending; set on callback
);

CREATE INDEX IF NOT EXISTS idx_onboard_nonces_expires
    ON onboard_nonces (expires_at)
    WHERE consumed_at IS NULL;

COMMENT ON TABLE contracts IS
    'Signed agreement artifacts linking a principal to a Stripe Quote. '
    'One row per pending/accepted/voided contract. stripe_quote_id is the idempotency key. '
    'Sprint 006 E.2/G68; E.3/G69 sets signed_at on payment_intent.succeeded.';

COMMENT ON TABLE onboard_nonces IS
    'Transient state for in-progress GitHub OAuth onboarding flows. '
    'Nonce = 32-byte hex random; sent as OAuth state= parameter. TTL = 10 minutes. '
    'Sprint 006 E.2/G68.';
