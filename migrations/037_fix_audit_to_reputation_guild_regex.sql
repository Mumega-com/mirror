-- Migration 037: Fix audit_to_reputation() guild_scope extraction regex
-- Gate: TC08 E2E (learning loop)
-- Owner: Kasra
--
-- Problem:
--   audit_to_reputation() used `'^guild:([a-z0-9-]+):'` which captured only
--   the slug (e.g. 'alpha') from resource 'guild:alpha:match:{ref}'.
--   But guilds.id = 'guild:alpha' — the FK requires the full prefixed ID.
--   Result: INSERT into reputation_events with guild_scope='alpha' → FK violation.
--
-- Root cause in learning.py (also fixed in same sprint):
--   _emit_via_audit_chain encoded f'guild:{guild_scope}:match:{ref}' where
--   guild_scope was already 'guild:alpha', producing 'guild:guild:alpha:match:...'
--   That has been corrected to f'{guild_scope}:match:{ref}'.
--
-- Resource naming contract after this migration:
--   guild_scope set  → '{guild_id}:match:{evidence_ref}'  e.g. 'guild:alpha:match:42'
--   guild_scope None → 'match:{evidence_ref}'
--
-- Trigger fix:
--   Old regex: '^guild:([a-z0-9-]+):'     → captures 'alpha'        (wrong)
--   New regex: '^(guild:[a-z0-9-]+):match:'  → captures 'guild:alpha'  (correct)

CREATE OR REPLACE FUNCTION audit_to_reputation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    -- Weight constants (tuning only — never in logic)
    w_task_completed  CONSTANT NUMERIC := +1.0;
    w_task_failed     CONSTANT NUMERIC := -1.0;
    w_task_abandoned  CONSTANT NUMERIC := -0.5;
    w_review_approved CONSTANT NUMERIC := +0.8;
    w_review_rejected CONSTANT NUMERIC := -0.8;
    w_peer_endorsed   CONSTANT NUMERIC := +0.5;
    w_peer_flagged    CONSTANT NUMERIC := -0.5;

    v_event_type TEXT;
    v_weight     NUMERIC;
    v_guild      TEXT;
BEGIN
    -- Stream whitelist (F-11 fix from migration 033)
    IF NEW.stream_id NOT IN ('dispatcher', 'kernel', 'classifier') THEN
        RETURN NEW;
    END IF;

    CASE NEW.action
        WHEN 'task_completed'  THEN v_event_type := 'task_completed';  v_weight := w_task_completed;
        WHEN 'task_failed'     THEN v_event_type := 'task_failed';     v_weight := w_task_failed;
        WHEN 'task_abandoned'  THEN v_event_type := 'task_abandoned';  v_weight := w_task_abandoned;
        WHEN 'review_approved' THEN v_event_type := 'review_approved'; v_weight := w_review_approved;
        WHEN 'review_rejected' THEN v_event_type := 'review_rejected'; v_weight := w_review_rejected;
        WHEN 'peer_endorsed'   THEN v_event_type := 'peer_endorsed';   v_weight := w_peer_endorsed;
        WHEN 'peer_flagged'    THEN v_event_type := 'peer_flagged';    v_weight := w_peer_flagged;
        ELSE RETURN NEW;  -- not a reputation-relevant action; skip silently
    END CASE;

    -- Infer guild_scope from resource naming contract.
    -- Patterns:
    --   '{guild_id}:match:{ref}'  → v_guild = '{guild_id}'  (scoped, e.g. 'guild:alpha')
    --   'match:{ref}'             → v_guild = NULL           (global)
    --
    -- Regex captures the full guild ID including 'guild:' prefix, stopping at ':match:'.
    -- Returns NULL (not garbage) when resource does not match — safe FK insert.
    v_guild := substring(NEW.resource FROM '^(guild:[a-z0-9-]+):match:');

    -- evidence_ref = audit_events.id (UUID) — provides full audit trail linkage.
    INSERT INTO reputation_events (holder_id, event_type, weight, guild_scope, evidence_ref, recorded_at)
    VALUES (NEW.actor_id, v_event_type, v_weight, v_guild, NEW.id::TEXT, NEW.ts);

    RETURN NEW;
END;
$$;
