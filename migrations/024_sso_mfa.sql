-- Migration 024: SSO (SAML 2.0 + OIDC) + SCIM + MFA (§2B.1)
-- Depends on: 023_identity_roles.sql (principals, role_assignments)
-- Gate: Athena G6
-- Owner: Kasra
--
-- Three new tables:
--   idp_configurations    — one row per configured IdP per tenant
--   sso_identity_links    — IdP external_subject ↔ principal_id mapping
--   mfa_enrolled_methods  — TOTP and WebAuthn credentials per principal
-- Plus:
--   idp_group_role_map    — translates IdP group claims → role_assignments

-- ── IdP Configurations ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS idp_configurations (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    protocol         TEXT NOT NULL CHECK (protocol IN ('saml', 'oidc')),
    display_name     TEXT NOT NULL,                -- e.g. "Google Workspace"

    -- SAML fields (null for OIDC)
    metadata_url     TEXT,                         -- IdP SAML metadata URL
    entity_id        TEXT,                         -- SP entity ID (our side)
    acs_url          TEXT,                         -- Assertion Consumer Service URL

    -- OIDC fields (null for SAML)
    client_id        TEXT,                         -- OAuth2 client ID
    client_secret_ref TEXT,                        -- Vault secret ref (never store plaintext)
    authorization_url TEXT,
    token_url        TEXT,
    userinfo_url     TEXT,
    jwks_url         TEXT,

    group_claim_path TEXT NOT NULL DEFAULT 'groups', -- JSON path in assertion/claims for groups
    enabled          BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, display_name)
);

CREATE INDEX IF NOT EXISTS idx_idp_config_tenant  ON idp_configurations (tenant_id) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_idp_config_protocol ON idp_configurations (tenant_id, protocol);

-- ── SSO Identity Links ─────────────────────────────────────────────────────────
-- Maps an IdP's stable subject identifier to an SOS principal.
-- UNIQUE(idp_id, external_subject) ensures each IdP identity links to exactly one principal.
CREATE TABLE IF NOT EXISTS sso_identity_links (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    idp_id           TEXT NOT NULL REFERENCES idp_configurations(id) ON DELETE CASCADE,
    external_subject TEXT NOT NULL,               -- IdP's stable user identifier (NameID / sub)
    principal_id     TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    email            TEXT,                        -- email from IdP assertion at link time
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idp_id, external_subject)
);

CREATE INDEX IF NOT EXISTS idx_sso_links_principal ON sso_identity_links (principal_id);
CREATE INDEX IF NOT EXISTS idx_sso_links_tenant    ON sso_identity_links (tenant_id);

-- ── MFA Enrolled Methods ───────────────────────────────────────────────────────
-- TOTP and WebAuthn credentials per principal. Multiple methods allowed.
CREATE TABLE IF NOT EXISTS mfa_enrolled_methods (
    id              TEXT PRIMARY KEY,
    principal_id    TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    method          TEXT NOT NULL CHECK (method IN ('totp', 'webauthn')),

    -- TOTP fields (null for WebAuthn)
    secret_ref      TEXT,               -- Vault ref to TOTP secret (never stored plaintext here)

    -- WebAuthn fields (null for TOTP)
    credential_id   TEXT,               -- WebAuthn credential ID (base64url)
    public_key      TEXT,               -- COSE public key bytes (base64url)
    aaguid          TEXT,               -- authenticator AAGUID
    sign_count      BIGINT DEFAULT 0,   -- monotonic counter for replay prevention

    label           TEXT NOT NULL DEFAULT 'default',   -- user-facing name ("iPhone", "YubiKey 5")
    enabled         BOOLEAN NOT NULL DEFAULT true,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (principal_id, method, label)
);

CREATE INDEX IF NOT EXISTS idx_mfa_methods_principal ON mfa_enrolled_methods (principal_id) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_mfa_credential_id     ON mfa_enrolled_methods (credential_id) WHERE credential_id IS NOT NULL;

-- ── IdP Group → Role Map ────────────────────────────────────────────────────────
-- Translates IdP group names (from claims) into role_assignments.
-- Processed at login time and on SCIM group update events.
CREATE TABLE IF NOT EXISTS idp_group_role_map (
    id          TEXT PRIMARY KEY,
    idp_id      TEXT NOT NULL REFERENCES idp_configurations(id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    group_name  TEXT NOT NULL,              -- exact string from IdP group claim
    role_id     TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idp_id, group_name, role_id)
);

CREATE INDEX IF NOT EXISTS idx_idp_group_map_idp ON idp_group_role_map (idp_id);
