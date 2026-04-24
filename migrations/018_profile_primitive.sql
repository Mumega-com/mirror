-- Migration 018: profile primitive tables (§11 + Burst 2B-3)
-- Phase 6 / Burst 2B — profile self-serve surface + PIPEDA/GDPR data rights
-- Owner: Athena (schema) · Kasra (service)
-- §11 patches applied: granted_at NOT NULL, workspace_id on tool_connections,
--   status CHECK on tool_connections, retain_reason on profile_requests (PIPEDA),
--   profile_export_jobs.status CHECK.

CREATE TABLE IF NOT EXISTS profiles (
    id              TEXT PRIMARY KEY,           -- principal_id (matches contacts.id or agent id)
    workspace_id    TEXT NOT NULL,
    slug            TEXT NOT NULL,
    display_name    TEXT,
    email           TEXT,
    avatar_url      TEXT,
    bio             TEXT,
    tier            TEXT NOT NULL DEFAULT 'public'
                        CHECK (tier IN ('public','squad','project','role','entity','private')),
    entity_id       TEXT,
    permitted_roles TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_profiles_workspace ON profiles (workspace_id);
CREATE INDEX IF NOT EXISTS idx_profiles_slug      ON profiles (slug);


-- Consent log — one row per consent grant/revoke event (append-only)
CREATE TABLE IF NOT EXISTS profile_consents (
    id              BIGSERIAL PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,
    consent_type    TEXT NOT NULL,              -- e.g. 'marketing_email', 'data_processing'
    granted         BOOLEAN NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL,       -- NOT NULL: consent without a date is unenforceable
    revoked_at      TIMESTAMPTZ,
    source          TEXT,                       -- 'magic_link', 'api', 'import'
    ip_address      TEXT,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_profile_consents_profile
    ON profile_consents (profile_id, consent_type);


-- Access log — who accessed profile data above public tier
CREATE TABLE IF NOT EXISTS profile_access_log (
    id              BIGSERIAL PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,
    viewer_id       TEXT NOT NULL,              -- principal or agent id
    viewer_role     TEXT NOT NULL,
    resource        TEXT NOT NULL,              -- e.g. 'engrams', 'contracts', 'goals'
    purpose         TEXT,
    accessed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_profile_access_log_profile
    ON profile_access_log (profile_id, accessed_at DESC);


-- Tool connections — OAuth/API integrations the person has authorized
CREATE TABLE IF NOT EXISTS profile_tool_connections (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,              -- required: prevents cross-workspace leak
    tool_name       TEXT NOT NULL,              -- e.g. 'google_calendar', 'fireflies'
    oauth_token_ref TEXT,                       -- Vault path (never plaintext)
    scopes          TEXT[],
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','revoked','expired')),
    connected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    UNIQUE (profile_id, tool_name)
);

CREATE INDEX IF NOT EXISTS idx_profile_tool_connections_profile
    ON profile_tool_connections (profile_id, status);


-- Data requests — erasure, export, correction (PIPEDA/GDPR request intake)
CREATE TABLE IF NOT EXISTS profile_requests (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,
    type            TEXT NOT NULL
                        CHECK (type IN ('erasure','export','correction','access')),
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','in_progress','completed','rejected')),
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    retain_reason   TEXT,                       -- PIPEDA legal basis for retained rows on erasure
    receipt         JSONB                       -- JSON breakdown: deleted rows, retained rows, legal basis per row
);

CREATE INDEX IF NOT EXISTS idx_profile_requests_profile
    ON profile_requests (profile_id, type, status);


-- Export jobs — async zip generation
CREATE TABLE IF NOT EXISTS profile_export_jobs (
    id              TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL REFERENCES profile_requests(id) ON DELETE CASCADE,
    profile_id      TEXT NOT NULL,
    workspace_id    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','complete','failed')),
    signed_url      TEXT,                       -- delivered to profile email once complete
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_profile_export_jobs_profile
    ON profile_export_jobs (profile_id, status);


-- Auto-update updated_at on profiles
CREATE OR REPLACE FUNCTION profiles_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON profiles;
CREATE TRIGGER trg_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION profiles_set_updated_at();
