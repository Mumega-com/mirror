-- Migration 028: Transfer postgres-owned tables to mirror user
-- Gate: Athena G12 (prerequisite — migrate.py runs as mirror, can't ALTER postgres-owned tables)
-- Owner: Kasra
-- Applied-by: postgres superuser (one-time fix; future migrations run as mirror)
--
-- Problem: mirror_engrams and 6 other tables were created by postgres (initial
-- schema bootstrap). migrate.py runs as `mirror` user. ALTER TABLE on a
-- postgres-owned table requires superuser privileges — causes permission error.
--
-- Fix: transfer ownership to mirror once. All future migrations (including
-- ALTER TABLE ADD COLUMN, CREATE INDEX) on these tables will succeed as mirror.
--
-- Tables being transferred (all were postgres-owned as of 2026-04-25):
--   mirror_code_nodes, mirror_council_history, mirror_engrams,
--   mirror_pulse_history, mirror_state_audit_log, mirror_tokens,
--   mirror_workspaces, tasks
--
-- Run as: psql -U postgres -d mirror -f 028_table_ownership.sql
-- DO NOT run via migrate.py (mirror user cannot change ownership of postgres tables)

ALTER TABLE mirror_code_nodes       OWNER TO mirror;
ALTER TABLE mirror_council_history  OWNER TO mirror;
ALTER TABLE mirror_engrams          OWNER TO mirror;
ALTER TABLE mirror_pulse_history    OWNER TO mirror;
ALTER TABLE mirror_state_audit_log  OWNER TO mirror;
ALTER TABLE mirror_tokens           OWNER TO mirror;
ALTER TABLE mirror_workspaces       OWNER TO mirror;
ALTER TABLE tasks                   OWNER TO mirror;
