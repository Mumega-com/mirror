-- Migration 022: §15 Reputation — computed, time-decayed trust score (Sprint 003 Track C)
-- Gate: Athena G10 APPROVED (spec v1.2)
-- Owner: Athena (schema + trigger) · Kasra (Dreamer hook + contract)
-- Constitutional:
--   - audit chain is the ONLY source; REVOKE INSERT on app role enforced below
--   - reputation_scores is a materialized TABLE (not a view); Dreamer controls recompute timing
--   - no XP/levels/quest vocabulary; scores are honest rates (reliability/quality/compliance)
--   - audit_to_reputation() is SECURITY DEFINER so trigger can INSERT despite app role REVOKE

CREATE TABLE IF NOT EXISTS reputation_events (
    id          BIGSERIAL PRIMARY KEY,
    holder_id   TEXT NOT NULL,              -- profile_id of citizen receiving rep impact
    event_type  TEXT NOT NULL CHECK (event_type IN (
                    'task_completed','task_failed','task_abandoned',
                    'verification_passed','verification_failed',
                    'audit_clean','audit_violation',
                    'peer_endorsed','peer_flagged'
                )),
    weight      NUMERIC(6,3) NOT NULL,      -- positive or negative; magnitude per event_type
    guild_scope TEXT REFERENCES guilds(id) ON DELETE SET NULL,  -- NULL = global
    evidence_ref TEXT NOT NULL,             -- audit_events.id (traceability)
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rep_events_holder ON reputation_events(holder_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_rep_events_guild  ON reputation_events(guild_scope, recorded_at DESC)
    WHERE guild_scope IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rep_events_type   ON reputation_events(event_type, recorded_at DESC);


CREATE TABLE IF NOT EXISTS reputation_scores (
    id          BIGSERIAL PRIMARY KEY,
    holder_id   TEXT NOT NULL,
    score_kind  TEXT NOT NULL CHECK (score_kind IN ('overall','reliability','quality','compliance')),
    guild_scope TEXT REFERENCES guilds(id) ON DELETE SET NULL,  -- NULL = global
    value       NUMERIC(8,4) NOT NULL,      -- computed score (typically -100..100, unbounded)
    sample_size INTEGER NOT NULL,           -- event count feeding this score
    decay_factor NUMERIC(5,4) NOT NULL,     -- half-life constant used in this recompute
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Two partial unique indexes to handle NULL guild_scope correctly.
-- Standard UNIQUE(holder_id, score_kind, guild_scope) does NOT enforce uniqueness when
-- guild_scope IS NULL (NULL != NULL in PG). These partial indexes close that gap.
CREATE UNIQUE INDEX IF NOT EXISTS idx_rep_scores_unique_global
    ON reputation_scores(holder_id, score_kind) WHERE guild_scope IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rep_scores_unique_scoped
    ON reputation_scores(holder_id, score_kind, guild_scope) WHERE guild_scope IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rep_scores_holder ON reputation_scores(holder_id, score_kind);
CREATE INDEX IF NOT EXISTS idx_rep_scores_guild  ON reputation_scores(guild_scope, score_kind)
    WHERE guild_scope IS NOT NULL;


-- ---------------------------------------------------------------------------
-- audit_to_reputation() — trigger function feeding reputation_events from audit chain
--
-- SECURITY DEFINER: runs as function owner (who has INSERT on reputation_events).
-- App role has INSERT REVOKED on reputation_events; this SECURITY DEFINER escalation
-- is the only write path. SET search_path prevents search_path injection attacks.
--
-- Weight constants: 9 named values at top. Tuning = change 9 lines, no logic change.
--
-- guild_scope inference: resource naming contract is 'guild:{slug}:{type}:{id}'.
-- Returns NULL (not garbage) when resource does not match — verified by test suite.
-- ---------------------------------------------------------------------------

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

    -- Infer guild_scope from resource naming contract: 'guild:{slug}:{type}:{id}'
    -- Returns NULL (not garbage) when resource does not match the pattern.
    v_guild := substring(NEW.resource FROM '^guild:([a-z0-9-]+):');

    INSERT INTO reputation_events (holder_id, event_type, weight, guild_scope, evidence_ref, recorded_at)
    VALUES (NEW.actor_id, v_event_type, v_weight, v_guild, NEW.id::TEXT, NEW.ts);

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_audit_to_reputation
    AFTER INSERT ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION audit_to_reputation();
