-- Migration 026: Per-workspace DEK envelope encryption (§2B.4)
-- Gate: Athena G7
-- Owner: Kasra
-- Ref: docs/superpowers/plans/burst-2b/04-secrets-vault-dek.md
--
-- Introduces workspace_keys — one row per workspace (tenant).
-- The DEK (Data Encryption Key) is generated at workspace creation,
-- encrypted with the workspace's KEK (Key Encryption Key) from Vault,
-- and stored here. Services unwrap the DEK via Vault on each request;
-- the unwrapped DEK lives in memory for the request lifetime only.
--
-- Encryption algorithm: AES-256-GCM
-- KEK storage: Vault KV v2 at sos/dek/{workspace_id}/kek
-- DEK wrap format: 12-byte nonce || AES-GCM ciphertext (BYTEA)
--
-- Acceptance (Athena G7):
--   1. Per-workspace DEK roundtrip verified
--   2. Cross-workspace isolation (different DEK cannot decrypt)
--   3. Audit event on every encrypt/decrypt operation
--   4. audit-plaintext-secrets.py returns zero findings

CREATE TABLE IF NOT EXISTS workspace_keys (
    workspace_id          TEXT        PRIMARY KEY,
    dek_encrypted_with_kek BYTEA      NOT NULL,
    kek_ref               TEXT        NOT NULL,   -- Vault path: sos/dek/{workspace_id}/kek
    algorithm             TEXT        NOT NULL DEFAULT 'AES-256-GCM',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at            TIMESTAMPTZ             -- NULL until first rotation
);

-- Index for audits and rotation sweeps
CREATE INDEX IF NOT EXISTS idx_workspace_keys_rotated
    ON workspace_keys (rotated_at)
    WHERE rotated_at IS NOT NULL;

COMMENT ON TABLE workspace_keys IS
    '§2B.4 Per-workspace envelope encryption keys. '
    'DEK encrypted with KEK from Vault. One row per tenant/workspace.';

COMMENT ON COLUMN workspace_keys.dek_encrypted_with_kek IS
    'AES-256-GCM encrypted DEK: first 12 bytes = nonce, remainder = ciphertext.';

COMMENT ON COLUMN workspace_keys.kek_ref IS
    'Vault KV v2 path for the KEK. Format: sos/dek/{workspace_id}/kek';
