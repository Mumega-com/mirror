-- Migration 033: Audit chain reputation hardening (Sprint 005 P0-1)
-- Gate: G17b
-- Owner: Kasra
-- Fixes:
--   F-02 CRITICAL — learning._emit_reputation_event() bypassed audit chain
--   F-11 HIGH     — audit_to_reputation trigger fired on events from any stream
--
-- F-02 fix strategy:
--   learning.py now routes through _emit_via_audit_chain() → _emit_audit_sync()
--   which inserts into audit_events (stream_id='kernel'). The SECURITY DEFINER
--   trigger audit_to_reputation() fires and inserts into reputation_events.
--   This is the ONLY approved write path.
--
--   DB-level enforcement note (F-02b, Sprint 005 superuser migration):
--   REVOKE INSERT ON reputation_events FROM mirror is blocked because
--   reputation_events is owned by 'mirror' (created in migration 022 by mirror user).
--   PostgreSQL table owners retain all privileges regardless of REVOKE.
--   Full enforcement requires:
--     ALTER TABLE reputation_events OWNER TO postgres;  -- superuser
--     REVOKE INSERT ON reputation_events FROM mirror;
--     -- Then re-create audit_to_reputation as SECURITY DEFINER owned by postgres.
--   This is logged as F-02b in Sprint 005.
--
-- F-11 fix:
--   audit_to_reputation() now validates stream_id whitelist before creating
--   reputation events. Only 'dispatcher', 'kernel', 'classifier' streams are
--   trusted. All other streams are silently ignored.
--   Sprint 005 P0-1b: also enforce signature IS NOT NULL after AUDIT_SIGNING_KEY
--   is distributed to the matchmaker service.
--
-- Weight constants are unchanged from migration 022; only whitelist check added.

CREATE OR REPLACE FUNCTION audit_to_reputation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    -- Weight constants (tune here; do not scatter through CASE below)
    w_task_completed      CONSTANT NUMERIC := 1.0;
    w_task_failed         CONSTANT NUMERIC := -0.8;
    w_task_abandoned      CONSTANT NUMERIC := -0.3;
    w_verification_passed CONSTANT NUMERIC := 1.5;
    w_verification_failed CONSTANT NUMERIC := -1.2;
    w_audit_clean         CONSTANT NUMERIC := 0.2;
    w_audit_violation     CONSTANT NUMERIC := -3.0;
    w_peer_endorsed       CONSTANT NUMERIC := 0.5;
    w_peer_flagged        CONSTANT NUMERIC := -1.0;

    v_event_type TEXT;
    v_weight     NUMERIC;
    v_guild      TEXT;
BEGIN
    -- F-11: Stream whitelist enforcement.
    -- Only events from trusted kernel streams generate reputation events.
    -- This closes the attack surface where any stream could inject arbitrary
    -- action values (e.g. 'task_completed') to fabricate reputation gains.
    --
    -- Sprint 005 P0-1b: add `AND NEW.signature IS NOT NULL` once AUDIT_SIGNING_KEY
    -- is distributed to matchmaker service and kernel emission path.
    IF NEW.stream_id NOT IN ('dispatcher', 'kernel', 'classifier') THEN
        RETURN NEW;  -- silently skip; no reputation event created
    END IF;

    -- Whitelist check + weight assignment
    CASE NEW.action
        WHEN 'task_completed'      THEN v_event_type := 'task_completed';      v_weight := w_task_completed;
        WHEN 'task_failed'         THEN v_event_type := 'task_failed';         v_weight := w_task_failed;
        WHEN 'task_abandoned'      THEN v_event_type := 'task_abandoned';      v_weight := w_task_abandoned;
        WHEN 'verification_passed' THEN v_event_type := 'verification_passed'; v_weight := w_verification_passed;
        WHEN 'verification_failed' THEN v_event_type := 'verification_failed'; v_weight := w_verification_failed;
        WHEN 'audit_clean'         THEN v_event_type := 'audit_clean';         v_weight := w_audit_clean;
        WHEN 'audit_violation'     THEN v_event_type := 'audit_violation';     v_weight := w_audit_violation;
        WHEN 'peer_endorsed'       THEN v_event_type := 'peer_endorsed';       v_weight := w_peer_endorsed;
        WHEN 'peer_flagged'        THEN v_event_type := 'peer_flagged';        v_weight := w_peer_flagged;
        ELSE RETURN NEW;  -- not a reputation-relevant action; skip silently
    END CASE;

    -- Infer guild_scope from resource naming contract.
    -- Patterns:
    --   'guild:{slug}:match:{ref}' → v_guild = '{slug}'  (scoped)
    --   'match:{ref}'              → v_guild = NULL       (global)
    v_guild := substring(NEW.resource FROM '^guild:([a-z0-9-]+):');

    -- evidence_ref = audit_events.id (UUID) — provides full audit trail linkage.
    INSERT INTO reputation_events (holder_id, event_type, weight, guild_scope, evidence_ref, recorded_at)
    VALUES (NEW.actor_id, v_event_type, v_weight, v_guild, NEW.id::TEXT, NEW.ts);

    RETURN NEW;
END;
$$;

-- Trigger is already created in migration 022; CREATE OR REPLACE above is sufficient.
-- (trigger definition on audit_events is unchanged; only the function body is updated)
