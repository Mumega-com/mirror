-- Migration 030: §16 Matchmaking — quests, quest_vectors, citizen_vectors, match_history
-- Gate: Athena G15 (A.3 trigger — submitted post A.3 contract ship)
-- Owner: Kasra
--
-- Tables:
--   quests          — citizen work units; tier-gated (T1/T2/T3/T4)
--   quest_vectors   — 16D Vertex Flash Lite scoring per quest (A.4 auto-extract)
--   citizen_vectors — 16D per citizen; evolved via agent_dna.evolve() (A.6)
--   match_history   — assignment record; outcomes feed A.6 learning loop
--
-- Design notes:
--   - quests.tier CHECK enforces T1/T2/T3/T4 (Athena G13 build-time condition)
--   - 16D vectors stored as FLOAT8[] (16 elements); psycopg2 reads as Python list
--   - match_history.outcome NULL = pending; 'accepted'/'rejected'/'abandoned' on close
--   - No pgvector dependency — cosine computed in Python (numpy) for now;
--     migrate to pgvector <=> when quest_vectors volume warrants indexing (future sprint)
--
-- Run as: psql -U mirror -d mirror -f 030_matchmaking_schema.sql  (or via migrate.py)


-- ── quests ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quests (
    id                   TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    title                TEXT        NOT NULL,
    description          TEXT        NOT NULL DEFAULT '',
    tier                 TEXT        NOT NULL CHECK (tier IN ('T1','T2','T3','T4')),
    guild_scope          TEXT        REFERENCES guilds(id) ON DELETE SET NULL,  -- NULL = global
    required_capabilities JSONB      NOT NULL DEFAULT '[]',  -- [{kind, ref, action}, ...]
    status               TEXT        NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open','assigned','closed','cancelled')),
    created_by           TEXT        NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_quests_status ON quests(status, tier);
CREATE INDEX IF NOT EXISTS idx_quests_guild  ON quests(guild_scope, status)
    WHERE guild_scope IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_quests_tier   ON quests(tier, status);


-- ── quest_vectors — 16D per quest (A.4 Vertex Flash Lite auto-extract) ─────────

CREATE TABLE IF NOT EXISTS quest_vectors (
    quest_id     TEXT        PRIMARY KEY REFERENCES quests(id) ON DELETE CASCADE,
    vector       FLOAT8[]    NOT NULL,   -- exactly 16 elements; enforced by application
    model        TEXT        NOT NULL DEFAULT 'vertex-flash-lite',
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN quest_vectors.vector IS
    '16D scoring from Vertex Flash Lite per §16 spec. '
    'Dimensions correspond to the 16-axis alignment schema in agent_dna. '
    'Application code enforces array length = 16.';


-- ── citizen_vectors — 16D per citizen; evolved by A.6 learning loop ──────────

CREATE TABLE IF NOT EXISTS citizen_vectors (
    holder_id    TEXT        PRIMARY KEY,
    vector       FLOAT8[]    NOT NULL,   -- exactly 16 elements
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN citizen_vectors.vector IS
    '16D citizen alignment vector; evolves via agent_dna.evolve() after match outcomes. '
    'Cold-start citizens have no row; matchmaking returns 0.5 (neutral cosine) when absent.';


-- ── match_history — assignment record + outcome ────────────────────────────────

CREATE TABLE IF NOT EXISTS match_history (
    id              BIGSERIAL   PRIMARY KEY,
    quest_id        TEXT        NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    candidate_id    TEXT        NOT NULL,
    composite_score FLOAT8      NOT NULL,    -- Stage 4 scalarized score at assignment time
    offer_count     INTEGER     NOT NULL DEFAULT 0,  -- times this candidate was offered this quest
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome         TEXT        CHECK (outcome IN ('accepted','rejected','abandoned')),
    outcome_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_match_history_candidate ON match_history(candidate_id, assigned_at DESC);
CREATE INDEX IF NOT EXISTS idx_match_history_quest     ON match_history(quest_id, assigned_at DESC);
CREATE INDEX IF NOT EXISTS idx_match_history_outcome   ON match_history(candidate_id, outcome)
    WHERE outcome IS NOT NULL;
