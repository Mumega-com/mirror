-- Migration 050: gtm.principal_state — Sprint 012 OmniA LOCK-A
-- Gate: Kasra (Athena DECISION-1: state ≠ events, separate table)
--
-- Persistent assertions about principals (burnout risk, comeback window, etc.)
-- NOT events. Events go in gtm.actions. State goes here.
-- LOCK-A: expires_at NOT NULL (mandatory), principal_visibility_only DEFAULT TRUE.

CREATE TABLE IF NOT EXISTS gtm.principal_state (
    id          TEXT        PRIMARY KEY DEFAULT 'ps_' || gen_random_uuid()::text,
    principal_id TEXT       NOT NULL,
    state_class TEXT        NOT NULL,
    value       JSONB       NOT NULL DEFAULT '{}',
    principal_visibility_only BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  TEXT        NOT NULL,
    tenant_id   TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_principal_state_principal
    ON gtm.principal_state (principal_id, state_class);
CREATE INDEX IF NOT EXISTS idx_principal_state_expires
    ON gtm.principal_state (expires_at) WHERE expires_at > now();
CREATE INDEX IF NOT EXISTS idx_principal_state_tenant
    ON gtm.principal_state (tenant_id);

ALTER TABLE gtm.principal_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_principal_state_policy ON gtm.principal_state
    FOR ALL TO mumega_app_role
    USING (tenant_id = current_setting('app.tenant_id', true));

COMMENT ON TABLE gtm.principal_state IS
    'Persistent state assertions about principals. Athena DECISION-1: state ≠ events. '
    'LOCK-A: expires_at NOT NULL, principal_visibility_only DEFAULT TRUE. Sprint 012.';
