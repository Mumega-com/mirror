-- Migration 043: DB-level TTL clamp on saml_used_assertions.not_on_or_after
-- Gate: Athena G63
-- Owner: Kasra
--
-- Context:
--   _record_saml_assertion() (Sprint 005 G34 / F-20) already clamps not_on_or_after
--   to 24 hours from now() in application code (Python, line ~734 in sso.py).
--   This migration adds a DB CHECK constraint as defense-in-depth — a second layer
--   that rejects any INSERT that bypasses the application clamp (e.g., direct DB
--   write, schema drift, or future code path that omits the Python clamp).
--
-- Constraint: not_on_or_after <= now() + interval '25 hours'
--   The 25-hour ceiling (24h + 1h buffer) accommodates legitimate clock skew
--   between the application server and the DB host without rejecting valid assertions.
--   Assertions with TTL > 25h from insert time are a pre-poisoning attack vector
--   and must be rejected.
--
-- Note: CHECK constraints using now() are STABLE (not IMMUTABLE) in PostgreSQL.
--   They evaluate at statement execution time — this is correct and intended.
--   Existing rows are NOT retroactively checked (ADD CONSTRAINT does not recheck
--   pre-existing rows by default; use VALIDATE CONSTRAINT separately if needed).

ALTER TABLE saml_used_assertions
    ADD CONSTRAINT saml_used_assertions_max_ttl_25h
    CHECK (not_on_or_after <= now() + interval '25 hours');
