-- Migration 053: Mirror token issuance hardening
--
-- DB-issued Mirror workspace tokens must never become admin credentials. Root
-- admin remains MIRROR_ADMIN_TOKEN / system auth only; workspace tokens are
-- constrained to workload scopes.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'mirror_tokens_token_type_allowed'
          AND conrelid = 'mirror_tokens'::regclass
    ) THEN
        ALTER TABLE mirror_tokens
            ADD CONSTRAINT mirror_tokens_token_type_allowed
            CHECK (token_type IN ('agent', 'squad', 'readonly'))
            NOT VALID;
    END IF;
END $$;

-- Belt-and-suspenders: existing admin rows are no longer resolvable in
-- application code. Keep them inert so future UPDATEs cannot accidentally
-- revalidate them under the new constraint.
UPDATE mirror_tokens
SET active = false,
    token_type = 'readonly'
WHERE token_type = 'admin';

ALTER TABLE mirror_tokens
    VALIDATE CONSTRAINT mirror_tokens_token_type_allowed;
