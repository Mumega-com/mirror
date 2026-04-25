-- Migration 021: §14 Inventory — unified capability index (Sprint 003 Track C)
-- Gate: Athena G9 APPROVED (spec v1.1)
-- Owner: Athena (schema) · Kasra (contract + verifiers + reconciler)
-- Constitutional: tier=private; soft-pointer hub table; no FK enforcement across heterogeneous sources.
-- Integrity: per-kind verifiers + reconciler job (1h cycle); last_verified_at + verify_attempt_count
--   for staleness detection. Hard delete of orphaned/revoked rows after 30-day grace.

CREATE TABLE IF NOT EXISTS inventory_grants (
    grant_id            TEXT PRIMARY KEY,   -- e.g. 'inv:tool:loom:mcp__sos__send'
    holder_type         TEXT NOT NULL CHECK (holder_type IN ('human','agent','squad','guild')),
    holder_id           TEXT NOT NULL,      -- profile_id, agent slug, squad_id, or guild_id
    capability_kind     TEXT NOT NULL CHECK (capability_kind IN (
                            'credential','tool','automation','template',
                            'oauth_connection','guild_role','data_access','mcp_server'
                        )),
    capability_ref      TEXT NOT NULL,      -- soft pointer into source domain
    source_domain       TEXT NOT NULL,      -- 'd1:tokens','plugin:yaml','ghl:workflows','fs:sos/skills','pg:profile_tool_connections'
    scope               JSONB,              -- per-action constraints {"read_only": true, "rate_limit": "100/h"}
    granted_by          TEXT NOT NULL,      -- profile_id of grantor
    granted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ,        -- nullable; soft TTL
    last_verified_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    verify_attempt_count SMALLINT NOT NULL DEFAULT 0,   -- incremented on failed verify, reset on success
    last_error          TEXT,               -- last verifier error message (cleared on success)
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','stale','orphaned','revoked','expired')),
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Holder lookup (hot path — session resolution)
CREATE INDEX IF NOT EXISTS idx_inv_holder      ON inventory_grants(holder_type, holder_id, status);
CREATE INDEX IF NOT EXISTS idx_inv_holder_kind ON inventory_grants(holder_type, holder_id, capability_kind, status);

-- Capability lookup (who has access to a given capability)
CREATE INDEX IF NOT EXISTS idx_inv_capability  ON inventory_grants(capability_kind, capability_ref);

-- Reconciler scan (oldest unverified active grants first)
CREATE INDEX IF NOT EXISTS idx_inv_verify      ON inventory_grants(last_verified_at) WHERE status = 'active';

-- Orphan/reap eligibility
CREATE INDEX IF NOT EXISTS idx_inv_orphaned    ON inventory_grants(status, updated_at)
    WHERE status IN ('orphaned','revoked','expired');

-- Stuck verifier detection (operator query: attempt_count > 10 AND status = 'active')
CREATE INDEX IF NOT EXISTS idx_inv_stuck       ON inventory_grants(verify_attempt_count)
    WHERE status = 'active' AND verify_attempt_count > 0;

-- One active grant per (holder, kind, ref). Re-grants use ON CONFLICT DO UPDATE.
CREATE UNIQUE INDEX IF NOT EXISTS idx_inv_unique_active
    ON inventory_grants(holder_type, holder_id, capability_kind, capability_ref)
    WHERE status = 'active';

CREATE OR REPLACE FUNCTION inv_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_inv_updated_at
    BEFORE UPDATE ON inventory_grants
    FOR EACH ROW EXECUTE FUNCTION inv_touch_updated_at();
