-- Migration 014: Token issuance API
-- Replaces flat tenant_keys.json with a proper DB-backed token store.

CREATE TABLE IF NOT EXISTS mirror_workspaces (
    id          TEXT PRIMARY KEY,               -- e.g. "ws-a3f9c2b1"
    slug        TEXT NOT NULL UNIQUE,           -- e.g. "sos-dev"
    name        TEXT NOT NULL,                  -- display name
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mirror_tokens (
    id           TEXT PRIMARY KEY,              -- e.g. "tok-a3f9c2b1"
    workspace_id TEXT NOT NULL REFERENCES mirror_workspaces(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,          -- sha256(plaintext_token)
    label        TEXT NOT NULL,                 -- human name e.g. "kasra-agent"
    token_type   TEXT NOT NULL DEFAULT 'agent', -- agent | squad | readonly | admin
    owner_id     TEXT,                          -- agent/squad name within workspace
    active       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS mirror_tokens_workspace_idx
    ON mirror_tokens (workspace_id)
    WHERE active = true;

CREATE INDEX IF NOT EXISTS mirror_tokens_hash_idx
    ON mirror_tokens (token_hash)
    WHERE active = true;
