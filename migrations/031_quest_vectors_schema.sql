-- Migration 031: quest_vectors schema extension — source + named_dims (A.4)
-- Gate: no separate gate needed (A.4 Kasra-owned plumbing; G15 covers matchmaking schema)
-- Owner: Kasra
--
-- Adds:
--   quest_vectors.source    TEXT — 'auto-extracted' | 'manual'
--   quest_vectors.named_dims JSONB — {"dim_name": score, ...} sidecar for human review
--
-- The 16D vector stays as FLOAT8[] (same order as named_dims keys below).
-- named_dims is the canonical dimension map: allows audit trail + tuning without
-- re-reading the raw vector array.
--
-- Dimension order (16D, fixed — must match citizen_vectors):
--   0  technical_depth      1  communication        2  reliability
--   3  creativity           4  analytical_rigor     5  scope_awareness
--   6  execution_speed      7  collaboration        8  documentation
--   9  mentorship          10  strategic_thinking  11  compliance
--  12  resilience          13  initiative          14  domain_breadth
--  15  innovation

ALTER TABLE quest_vectors
    ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'auto-extracted'
        CHECK (source IN ('auto-extracted', 'manual')),
    ADD COLUMN IF NOT EXISTS named_dims JSONB;

COMMENT ON COLUMN quest_vectors.source IS
    'auto-extracted = Vertex Flash Lite scored from description; '
    'manual = quest creator supplied the vector directly.';

COMMENT ON COLUMN quest_vectors.named_dims IS
    'JSONB sidecar: {"technical_depth": 0.72, "communication": 0.45, ...}. '
    'Ordered consistently with the FLOAT8[] vector column. '
    'NULL for manual-source rows (creator provides raw vector, no named breakdown).';
