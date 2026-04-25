-- Migration 041: TOTP replay ledger (mfa_used_codes)
-- Gate: Athena G27
-- Owner: Kasra
--
-- Context:
--   verify_totp() checks code validity but does not record the code as used.
--   Within the TOTP validity window (30 sec ± 1 step skew = 90 sec window),
--   the same code can be replayed by a second request. This migration adds
--   a replay ledger keyed by (principal_id, code_hash) with PRIMARY KEY to
--   make duplicate inserts fail atomically.
--
-- code_hash = sha256(principal_id || ':' || code || ':' || time_window_start)
--   - Bound to principal: two principals using same 6-digit code never collide.
--   - Bound to time window: same code in next window produces different hash,
--     so cross-window reuse is not a false positive.
--   - Full 32-byte sha256: no truncation, no birthday-bound collision risk.
--
-- Retention: 5 minutes (generous vs. max 90-sec TOTP validity window).
--   Cleanup: DELETE FROM mfa_used_codes WHERE used_at < now() - interval '5 minutes'
--
-- Access:
--   REVOKE INSERT, UPDATE, DELETE ON mfa_used_codes FROM PUBLIC.
--   Only the MFA service role (mirror_app or equivalent) has write privilege.
--   Append-only: no UPDATE is ever legitimate — a used code is used once.

CREATE TABLE mfa_used_codes (
    principal_id  TEXT      NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    code_hash     BYTEA     NOT NULL,
    used_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, code_hash)
);

-- Index for cleanup job: find rows by used_at efficiently
-- (Partial indexes using now() are not IMMUTABLE; plain btree index is correct)
CREATE INDEX mfa_used_codes_cleanup_idx ON mfa_used_codes (used_at);

-- Revoke public write access — append-only via MFA service role only
-- (Adjust role name to match deployment; 'mirror' is the app user in local/prod)
REVOKE INSERT, UPDATE, DELETE ON mfa_used_codes FROM PUBLIC;
GRANT INSERT ON mfa_used_codes TO mirror;
