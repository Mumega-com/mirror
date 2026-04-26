-- Migration 051: OAuth tenant registry
-- S013-B Stream B — tenant-at-first-OAuth-callback
--
-- LOCK-TENANT-A: composite (idp_provider, sub) key — never bare sub.
-- LOCK-TENANT-D: slug auto-derived, DB UNIQUE enforces no squatting,
--               rate-limit per (idp_provider, sub) enforced at app layer.
-- LOCK-TENANT-E: INSERT ON CONFLICT DO NOTHING — idempotent upsert.
-- LOCK-AUDIT-1:  one DCR client per tenant via dcr_client_id UNIQUE.

CREATE TABLE IF NOT EXISTS oauth_tenants (
    tenant_id       TEXT PRIMARY KEY,              -- SHA256(oauth:{idp}:{sub})[:32]
    idp_provider    TEXT NOT NULL,                 -- 'github' | 'google' | future
    sub             TEXT NOT NULL,                 -- IdP user ID (opaque string)
    slug            TEXT NOT NULL,                 -- URL-safe, auto-derived from display_name
    display_name    TEXT NOT NULL,
    email           TEXT,
    tier            TEXT NOT NULL DEFAULT 'free'
                        CHECK (tier IN ('free', 'starter', 'growth', 'scale')),
    agent_name      TEXT NOT NULL,                 -- e.g. 'alice-knight'
    -- LOCK-AUDIT-1: one DCR client per tenant
    dcr_client_id   TEXT,                          -- set when client registers via DCR
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- LOCK-TENANT-A: composite key prevents sub collision across providers
    UNIQUE (idp_provider, sub),
    -- LOCK-TENANT-D: slug uniqueness enforced at DB level
    UNIQUE (slug),
    -- LOCK-AUDIT-1: one DCR client per tenant
    UNIQUE (dcr_client_id)
);

-- Rate limit tracking for tenant creation abuse (LOCK-TENANT-D)
CREATE TABLE IF NOT EXISTS oauth_tenant_creation_log (
    idp_provider    TEXT NOT NULL,
    sub             TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Partition by (idp_provider, sub) for per-user rate counting
    PRIMARY KEY (idp_provider, sub, created_at)
);

CREATE INDEX IF NOT EXISTS idx_oauth_tenants_slug ON oauth_tenants (slug);
CREATE INDEX IF NOT EXISTS idx_oauth_tenants_email ON oauth_tenants (email);
