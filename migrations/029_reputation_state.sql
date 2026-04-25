-- Migration 029: §15 Reputation — Glicko-2 Bayesian state reshape (Sprint 004 A.2)
-- Gate: Athena G14 (prerequisite for Athena.A1)
-- Owner: Kasra (schema) · Athena (gate)
--
-- Summary:
--   reputation_scores TABLE → renamed to reputation_scores_legacy (backup)
--   reputation_state TABLE created — holds Glicko-2 (μ, φ, σ) posterior per citizen
--   reputation_scores VIEW created — derived as μ - 1.5·φ (LCB), backward-compat read surface
--
-- Constitutional constraints (preserved from G10):
--   1. audit chain is STILL the only source of reputation_events. Unchanged.
--   2. reputation_state is written ONLY by recompute_reputation_scores() Dreamer hook.
--      App role REVOKED INSERT on reputation_state (enforced below).
--      SECURITY DEFINER function is the sole writer — same pattern as G10.
--   3. σ is NEVER exposed outside kernel. The reputation_scores VIEW omits σ.
--      Any SELECT returning reputation_state must mask σ in non-kernel callers.
--   4. No XP/levels/quest vocabulary. Scores are Glicko-2 LCB — honest uncertainty-penalised rating.
--
-- Glicko-2 defaults (Glickman 2012):
--   μ₀ = 0.0               (neutral start on Glicko-2 scale)
--   φ₀ = 350 / 173.7178 ≈ 2.014732  (RD start — high uncertainty)
--   σ₀ = 0.06              (volatility start)
--
-- Backward compat:
--   get_score() callers read reputation_scores VIEW unchanged.
--   View exposes: holder_id, score_kind, guild_scope, value (LCB), sample_size,
--                 decay_factor (0.0 — not applicable in Glicko-2), computed_at.
--   Callers MUST NOT depend on decay_factor ≠ 0 after this migration.
--
-- Run as: psql -U mirror -d mirror -f 029_reputation_state.sql  (or via migrate.py)


-- ── Step 1: Preserve existing materialized data ───────────────────────────────

ALTER TABLE IF EXISTS reputation_scores RENAME TO reputation_scores_legacy;


-- ── Step 2: reputation_state — Glicko-2 Bayesian posterior ───────────────────

CREATE TABLE IF NOT EXISTS reputation_state (
    holder_id    TEXT        NOT NULL,
    kind         TEXT        NOT NULL CHECK (kind IN ('overall','reliability','quality','compliance')),
    guild_scope  TEXT        REFERENCES guilds(id) ON DELETE SET NULL,  -- NULL = global
    mu           NUMERIC(12,6) NOT NULL DEFAULT 0.0,       -- Glicko-2 rating (μ)
    phi          NUMERIC(12,6) NOT NULL DEFAULT 2.014732,  -- Glicko-2 RD (φ); 350/173.7178
    sigma        NUMERIC(12,8) NOT NULL DEFAULT 0.06,      -- Glicko-2 volatility (σ); kernel-private
    sample_size  INTEGER     NOT NULL DEFAULT 0,           -- event count processed in last update
    last_updated TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial unique indexes — same NULL semantics trap as G10 (PG NULL != NULL).
-- Standard UNIQUE(holder_id, kind, guild_scope) does NOT enforce uniqueness when
-- guild_scope IS NULL. Two partial indexes close the gap, matching idx_rep_scores_unique_*.
CREATE UNIQUE INDEX IF NOT EXISTS idx_rep_state_unique_global
    ON reputation_state(holder_id, kind) WHERE guild_scope IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rep_state_unique_scoped
    ON reputation_state(holder_id, kind, guild_scope) WHERE guild_scope IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rep_state_holder
    ON reputation_state(holder_id, kind);
CREATE INDEX IF NOT EXISTS idx_rep_state_guild
    ON reputation_state(guild_scope, kind) WHERE guild_scope IS NOT NULL;


-- ── Step 3: reputation_scores VIEW — backward-compat derived surface ──────────
--
-- value  = μ - 1.5·φ  (LCB, lower confidence bound, k=1.5 per §16 v1.1 spec)
-- T1/T2 matchmaking callers derive UCB = μ + k·φ in Python (no separate view needed).
-- decay_factor = 0.0  — Glicko-2 has no decay constant; 0.0 is a sentinel for compat.

CREATE OR REPLACE VIEW reputation_scores AS
SELECT
    holder_id,
    kind                         AS score_kind,
    guild_scope,
    (mu - 1.5 * phi)::NUMERIC(8,4)   AS value,
    sample_size,
    0.0::NUMERIC(5,4)            AS decay_factor,
    last_updated                 AS computed_at
FROM reputation_state;


-- ── Step 4: RBAC — REVOKE INSERT on reputation_state from app role ────────────
--
-- Mirrors the G10 constitutional constraint on reputation_events.
-- The app role 'mirror' must be able to SELECT (read LCB via view) but NEVER INSERT
-- directly. Dreamer hook recompute_reputation_scores() uses its own writer principal.
--
-- NOTE: If the 'mirror' user IS the owner/writer principal (Dreamer runs as mirror),
-- this REVOKE is intentionally a no-op — Dreamer writes via function call, not raw INSERT.
-- Adjust if a separate app_mirror role is introduced in a future migration.

REVOKE INSERT, UPDATE, DELETE ON reputation_state FROM PUBLIC;
