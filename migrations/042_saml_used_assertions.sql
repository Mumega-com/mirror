-- Migration 042: SAML assertion replay ledger (saml_used_assertions)
-- Gate: Athena G34
-- Owner: Kasra
--
-- Context:
--   process_saml_response() validates signature + issuer + audience + NotOnOrAfter
--   but does not record the assertion as consumed. Within the assertion's validity
--   window (NotOnOrAfter — typically minutes), the same assertion can be replayed.
--   This migration adds a replay ledger keyed by (assertion_id, idp_id) with
--   PRIMARY KEY to make duplicate inserts fail atomically. Same shape as F-09
--   TOTP replay ledger (migration 041).
--
-- assertion_id: from SAML <saml:Assertion ID="..."> attribute — globally unique
--   per SAML spec, but bound to idp_id in PK to handle rare same-ID reuse across
--   IdP deployments without false-positive replay rejection.
--
-- not_on_or_after: captured for cleanup job; assertions are already invalid past
--   this timestamp by the existing time-window check (defense-in-depth).
--
-- Retention: 1 hour past not_on_or_after; typical assertions expire in 5 minutes.
--   Cleanup: DELETE FROM saml_used_assertions WHERE not_on_or_after < now() - interval '1 hour'
--
-- Access:
--   REVOKE INSERT, UPDATE, DELETE ON saml_used_assertions FROM PUBLIC.
--   INSERT: assertion processing path writes consumed assertions.
--   DELETE: cleanup job removes expired assertions.
--   UPDATE: never permitted — append-only invariant.

CREATE TABLE saml_used_assertions (
    assertion_id      TEXT        NOT NULL,
    idp_id            TEXT        NOT NULL REFERENCES idp_configurations(id) ON DELETE CASCADE,
    used_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    not_on_or_after   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (assertion_id, idp_id)
);

-- Index for cleanup job: efficiently find expired assertions
CREATE INDEX saml_used_assertions_cleanup_idx ON saml_used_assertions (not_on_or_after);

-- Revoke public write access — only SSO service role (mirror) writes
REVOKE INSERT, UPDATE, DELETE ON saml_used_assertions FROM PUBLIC;
GRANT INSERT, DELETE, SELECT ON saml_used_assertions TO mirror;
