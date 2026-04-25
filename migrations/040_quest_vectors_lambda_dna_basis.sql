-- Migration 040: quest_vectors + citizen_vectors lambda_dna basis discipline
-- Gate: G_A4b (Sprint 005 Track A — basis alignment)
-- Owner: Kasra
--
-- Context:
--   quest_vectors.vector currently encodes a 16-dim work-skills taxonomy
--   (technical_depth, communication, reliability...). citizen_vectors encodes
--   the canonical lambda_dna basis (Torivers.16D.001: Stability, Variance Control,
--   Recursion Depth, Termination Certainty, Internal Consistency, Context Retention,
--   Constraint Adherence, Error Containment, Intent Alignment, Structural Yield,
--   Dependency Discipline, Action Justification, Drift Resistance, Feedback Integration,
--   Cross-Agent Compatibility, Governance Compliance).
--
--   Stage 3 cosine(citizen_v, quest_v) across incompatible bases returns noise.
--   G_A4b aligns quest_vectors to lambda_dna basis.
--
-- Steps:
-- 1. Add vec_basis column to quest_vectors with CHECK (vec_basis = 'lambda_dna').
--    Existing rows get DEFAULT 'lambda_dna' — the column is present and enforcing
--    from migration time. vector+named_dims cleared (old basis invalid; backfill
--    required before Stage 3 is used in production).
-- 2. Add vec_basis column to citizen_vectors (already lambda_dna; makes it explicit).
-- 3. Backfill note: run sovereign/backfill_quest_vectors.py after migration to
--    re-extract all quest rows via Path A (Vertex Flash Lite, lambda_dna basis).
--    24 current rows × ~$0.003 total Vertex cost.
--
-- ON REGRESSION: any attempt to insert a quest_vector with vec_basis != 'lambda_dna'
--   will fail loudly at the CHECK constraint.

-- Step 1a: add vec_basis to quest_vectors
ALTER TABLE quest_vectors
    ADD COLUMN vec_basis TEXT NOT NULL DEFAULT 'lambda_dna'
    CONSTRAINT quest_vectors_vec_basis_check CHECK (vec_basis = 'lambda_dna');

-- Step 1b: clear stale work-skills vectors (old basis; requires backfill)
--   We set vector to a zero-filled 16-elem array as a sentinel, not NULL,
--   because the column is NOT NULL. Backfill replaces these with real lambda_dna
--   vectors. named_dims cleared so they don't mislead.
UPDATE quest_vectors
SET vector     = ARRAY_FILL(0.0::FLOAT8, ARRAY[16]),
    named_dims = NULL,
    source     = 'pending-lambda-dna-backfill';

-- Step 1c: update source CHECK to allow the sentinel value during migration window
-- (The source column currently only allows 'auto-extracted' | 'manual')
ALTER TABLE quest_vectors
    DROP CONSTRAINT quest_vectors_source_check;

ALTER TABLE quest_vectors
    ADD CONSTRAINT quest_vectors_source_check
    CHECK (source = ANY (ARRAY[
        'auto-extracted',
        'manual',
        'pending-lambda-dna-backfill'
    ]));

-- Step 2: add vec_basis to citizen_vectors (already lambda_dna; explicit tag)
ALTER TABLE citizen_vectors
    ADD COLUMN vec_basis TEXT NOT NULL DEFAULT 'lambda_dna'
    CONSTRAINT citizen_vectors_vec_basis_check CHECK (vec_basis = 'lambda_dna');
