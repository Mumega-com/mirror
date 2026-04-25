-- Migration 025: Principal erasure — PIPEDA "nullify + confiscate" model (§6.11)
-- Gate: Athena G11
-- Owner: Kasra
-- Ref: PIPEDA s.9 — anonymized records aren't ABOUT an identifiable person,
--      so retention of skeletal record + profile_id is legal.
--
-- Three additions to principals table:
--   1. status enum gains 'deactivated' (was: active, suspended, deprovisioned)
--   2. deactivated_at TIMESTAMPTZ — timestamp of erasure event
--
-- One new SQL function:
--   3. anonymize_profile(principal_id TEXT) — SECURITY DEFINER
--      NULLs each PII column individually with a separate audit_event per field.
--      Called inside deactivate_principal() transaction.
--
-- Hard-deletes sso_identity_links on deactivation (inside deactivate_principal()).
-- Does NOT touch mirror_engrams — system continuity records are not personal data.

-- ── 1. Extend status CHECK constraint on principals ───────────────────────────
ALTER TABLE principals
    DROP CONSTRAINT IF EXISTS principals_status_check;

ALTER TABLE principals
    ADD CONSTRAINT principals_status_check
    CHECK (status IN ('active', 'suspended', 'deprovisioned', 'deactivated'));

-- ── 2. Add deactivated_at column ──────────────────────────────────────────────
ALTER TABLE principals
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_principals_deactivated
    ON principals (deactivated_at)
    WHERE deactivated_at IS NOT NULL;

-- ── 3. anonymize_profile() — SECURITY DEFINER, per-field audit events ─────────
-- Called ONLY inside deactivate_principal(). Each PII column gets its own
-- audit_events row so the audit chain proves each field was individually cleared.
--
-- PII columns nulled: email, display_name
-- (phone, OAuth tokens, etc. are NOT in principals table — they live in
--  sso_identity_links which is hard-deleted, and mfa_enrolled_methods which
--  is disabled. If future columns hold PII, add them here.)

CREATE OR REPLACE FUNCTION anonymize_profile(p_principal_id TEXT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_email TEXT;
    v_display_name TEXT;
BEGIN
    -- Read current values for the audit log
    SELECT email, display_name
      INTO v_email, v_display_name
      FROM principals
     WHERE id = p_principal_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'principal % not found', p_principal_id;
    END IF;

    -- Null email with individual audit event
    IF v_email IS NOT NULL THEN
        UPDATE principals SET email = NULL WHERE id = p_principal_id;

        INSERT INTO audit_events (stream_id, actor_id, actor_type, action, resource, payload)
        VALUES (
            'erasure',
            'system',
            'system',
            'pii_field_nulled',
            'principal:' || p_principal_id || ':field:email',
            jsonb_build_object(
                'principal_id', p_principal_id,
                'field', 'email',
                'prior_value_hash', encode(sha256(v_email::bytea), 'hex'),
                'nulled_at', now()
            )
        );
    END IF;

    -- Null display_name with individual audit event
    IF v_display_name IS NOT NULL THEN
        UPDATE principals SET display_name = NULL WHERE id = p_principal_id;

        INSERT INTO audit_events (stream_id, actor_id, actor_type, action, resource, payload)
        VALUES (
            'erasure',
            'system',
            'system',
            'pii_field_nulled',
            'principal:' || p_principal_id || ':field:display_name',
            jsonb_build_object(
                'principal_id', p_principal_id,
                'field', 'display_name',
                'prior_value_hash', encode(sha256(v_display_name::bytea), 'hex'),
                'nulled_at', now()
            )
        );
    END IF;
END;
$$;

-- ── 4. deactivate_principal() — full erasure transaction ──────────────────────
-- Steps per Athena G11 spec:
--   (a) anonymize_profile() — PII nulled, per-field audit events emitted
--   (b) UPDATE principals status → 'deactivated', set deactivated_at
--   (c) DELETE sso_identity_links — IdP links cleared (CASCADE alone insufficient)
--   (d) profile_id (id column) preserved + indexed — reactivation token carrier
--   (e) mirror_engrams untouched — system continuity, not personal data
--   (f) Emit one final erasure_complete audit event for the record

CREATE OR REPLACE FUNCTION deactivate_principal(p_principal_id TEXT, p_requested_by TEXT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- (a) Null PII fields with per-field audit events
    PERFORM anonymize_profile(p_principal_id);

    -- (b) Set status deactivated + timestamp
    UPDATE principals
       SET status        = 'deactivated',
           deactivated_at = now(),
           updated_at    = now()
     WHERE id = p_principal_id;

    -- (c) Hard-delete IdP identity links — removes external subject mappings
    DELETE FROM sso_identity_links WHERE principal_id = p_principal_id;

    -- (d) Disable MFA methods (secrets already behind Vault refs, but disable for hygiene)
    UPDATE mfa_enrolled_methods
       SET enabled = false
     WHERE principal_id = p_principal_id;

    -- (f) Final erasure_complete audit event
    INSERT INTO audit_events (stream_id, actor_id, actor_type, action, resource, payload)
    VALUES (
        'erasure',
        p_requested_by,
        'human',
        'erasure_complete',
        'principal:' || p_principal_id,
        jsonb_build_object(
            'principal_id', p_principal_id,
            'requested_by', p_requested_by,
            'completed_at', now(),
            'model', 'nullify_and_confiscate',
            'retained', jsonb_build_array('id', 'tenant_id', 'principal_type', 'status', 'deactivated_at', 'created_at'),
            'deleted', jsonb_build_array('sso_identity_links')
        )
    );
END;
$$;
