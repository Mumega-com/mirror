-- Migration 034: frc_verdicts table (Sprint 005 P0-2, Gate G18)
-- Owner: Kasra
-- Fixes F-01 CRITICAL: FRC self-poisoning via classifier_run_log fabrication.
--
-- Attack vector:
--   Citizens can INSERT into mirror_engrams with owner_id=self and a fabricated
--   classifier_run_log JSON containing high confidence values. get_recent_verdicts()
--   reads classifier_run_log and derives 'aligned' verdict → bypasses Stage 2 FRC veto.
--
-- Fix:
--   1. Dedicated frc_verdicts table — sole authoritative verdict store.
--   2. REVOKE INSERT FROM PUBLIC (and mirror once ownership transferred — F-01b Sprint 005).
--   3. SECURITY DEFINER frc_emit_verdict() — sole write path; validates verdict enum.
--   4. get_recent_verdicts() in frc.py now reads frc_verdicts (not mirror_engrams).
--
-- F-01b (Sprint 005): Transfer frc_verdicts ownership to postgres, REVOKE INSERT FROM mirror.
--   Ed25519 signature enforcement requires AUDIT_SIGNING_KEY in classifier service.
--   signature column is present but nullable in v1; Sprint 005 makes it NOT NULL + verified.

CREATE TABLE IF NOT EXISTS frc_verdicts (
    id          BIGSERIAL PRIMARY KEY,
    engram_id   TEXT NOT NULL,           -- mirror_engrams.id (UUID as text)
    holder_id   TEXT NOT NULL,           -- citizen (profile_id / agent_id) being assessed
    verdict     TEXT NOT NULL CHECK (verdict IN ('aligned', 'degraded', 'failed')),
    issued_by   TEXT NOT NULL,           -- classifier run ID or 'system' for backfills
    -- Sprint 005 P0-2b: Ed25519 signature over (engram_id || verdict || issued_at)
    -- Requires AUDIT_SIGNING_KEY in classifier service. NULL allowed in v1.
    signature   BYTEA,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One canonical verdict per engram; newer supersedes via ON CONFLICT UPDATE
    UNIQUE (engram_id)
);

-- Access control: no direct INSERT from application layer
REVOKE INSERT ON frc_verdicts FROM PUBLIC;
-- F-01b note: REVOKE INSERT FROM mirror blocked because mirror owns this table.
-- Sprint 005 fix: ALTER TABLE frc_verdicts OWNER TO postgres; REVOKE INSERT FROM mirror.

CREATE INDEX IF NOT EXISTS idx_frc_verdicts_holder
    ON frc_verdicts (holder_id, issued_at DESC);

CREATE INDEX IF NOT EXISTS idx_frc_verdicts_engram
    ON frc_verdicts (engram_id);


-- SECURITY DEFINER sole write path for FRC verdicts.
--
-- Called by:
--   - Classifier service (via frc.save_verdict() in Python) after evaluating an engram
--   - Backfill jobs using issued_by='system'
--
-- Access control: REVOKE FROM PUBLIC ensures direct INSERT fails; only EXECUTE on this
-- function gives write access. Sprint 005: restrict EXECUTE to classifier service role.
--
-- ON CONFLICT DO UPDATE: if the same engram is re-evaluated (e.g. after re-classification),
-- the verdict is updated with the newer result. This is safe — FRC verdicts are point-in-time
-- assessments; the matchmaking Stage 2 uses issued_at to filter by recency.
CREATE OR REPLACE FUNCTION frc_emit_verdict(
    p_engram_id TEXT,
    p_holder_id TEXT,
    p_verdict   TEXT,
    p_issued_by TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    IF p_verdict NOT IN ('aligned', 'degraded', 'failed') THEN
        RAISE EXCEPTION 'frc_emit_verdict: invalid verdict %, must be aligned|degraded|failed', p_verdict;
    END IF;

    IF p_engram_id IS NULL OR p_holder_id IS NULL OR p_issued_by IS NULL THEN
        RAISE EXCEPTION 'frc_emit_verdict: engram_id, holder_id, issued_by must be non-null';
    END IF;

    INSERT INTO frc_verdicts (engram_id, holder_id, verdict, issued_by, issued_at)
    VALUES (p_engram_id, p_holder_id, p_verdict, p_issued_by, now())
    ON CONFLICT (engram_id) DO UPDATE SET
        verdict   = EXCLUDED.verdict,
        issued_by = EXCLUDED.issued_by,
        issued_at = EXCLUDED.issued_at
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;
