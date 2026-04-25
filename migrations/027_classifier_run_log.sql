-- Migration 027: classifier_run_log on mirror_engrams (K5 / A6 D7 prerequisite)
-- Gate: Athena G12
-- Owner: Kasra
-- Ref: sos/services/intake/classifier.py — ClassifierRunRecord
--
-- Adds classifier_run_log JSONB to mirror_engrams so the A6 lineage walker
-- can trace every model call that contributed to an engram's extraction.
--
-- Schema of each entry in the array:
--   {
--     "pass_number": 1,          -- 1 = flash-lite, 2 = escalated flash
--     "model": "gemini-2.5-flash-lite",
--     "billing_path": "vertex-adc",   -- "vertex-adc" | "gemini-api"
--     "confidence": 0.85,
--     "escalated": false,
--     "latency_ms": 312,
--     "input_tokens": 500,
--     "output_tokens": 200,
--     "cost_cents": 2,
--     "parse_error": null            -- null on success, string on failure
--   }
--
-- Nullable: engrams written before K5 shipped have no run log.
-- Indexed via GIN for A6 pattern queries (e.g. all engrams produced by a
-- given model or below a confidence threshold).
--
-- Acceptance (Athena G12):
--   1. Column exists on mirror_engrams with type JSONB, nullable
--   2. GIN index exists for JSON containment queries
--   3. Existing rows unaffected (NULL default)

ALTER TABLE mirror_engrams
    ADD COLUMN IF NOT EXISTS classifier_run_log JSONB DEFAULT NULL;

COMMENT ON COLUMN mirror_engrams.classifier_run_log IS
    'K5 classifier pass metadata array. Each element is a ClassifierRunRecord '
    '(model, billing_path, confidence, latency_ms, tokens, cost). '
    'NULL for engrams written before K5 shipped. Used by A6 lineage walker.';

CREATE INDEX IF NOT EXISTS idx_engrams_classifier_run_log
    ON mirror_engrams USING GIN (classifier_run_log)
    WHERE classifier_run_log IS NOT NULL;
