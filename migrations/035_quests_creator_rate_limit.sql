-- Migration 035: Quest creator rate limit trigger (Sprint 005 P0-3, Gate G16a)
-- Owner: Kasra
-- Fixes F-05 HIGH: Hungarian tick DoS via quest flooding.
--
-- Attack vector:
--   A bad actor creates thousands of open quests per day. Each matchmaker tick
--   calls rank_candidates() (5-stage pipeline, DB query) per open quest.
--   N=10000 quests × M=200 candidates = 10000 rank_candidates() calls per 30s tick → DoS.
--
-- Three-layer fix:
--   1. Application: LIMIT 100 in _fetch_open_quests() (MAX_QUESTS_PER_TICK env override).
--   2. DB trigger: max 10 open quests per created_by per 24h.
--   3. Application: 25s deadline on _build_matrix() via ThreadPoolExecutor.timeout.
--
-- This migration implements layer 2 (DB trigger).
-- Layers 1 + 3 are code changes in matchmaker.py.

CREATE OR REPLACE FUNCTION quests_creator_rate_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count   INTEGER;
    v_max     CONSTANT INTEGER := 10;
    v_window  CONSTANT INTERVAL := interval '1 day';
BEGIN
    -- Count open quests created by this principal in the last 24 hours.
    -- Uses an index-friendly range scan (created_at >= threshold).
    SELECT COUNT(*) INTO v_count
    FROM quests
    WHERE created_by = NEW.created_by
      AND status = 'open'
      AND created_at >= now() - v_window;

    IF v_count >= v_max THEN
        RAISE EXCEPTION
            'quest rate limit: creator % already has % open quest(s) in last 24h (max %)',
            NEW.created_by, v_count, v_max
            USING ERRCODE = 'insufficient_privilege';  -- maps to 403 in application layer
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_quests_creator_rate_limit
    BEFORE INSERT ON quests
    FOR EACH ROW
    EXECUTE FUNCTION quests_creator_rate_limit();

-- Index to make the rate-limit query efficient (created_by + created_at + status)
CREATE INDEX IF NOT EXISTS idx_quests_creator_open
    ON quests (created_by, created_at DESC)
    WHERE status = 'open';
