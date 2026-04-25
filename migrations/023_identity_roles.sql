-- Migration 023: Identity primitives — principals, roles, role_assignments (§1A)
-- Phase Sprint 003 / Burst 2B
-- Gate: Athena G6 (pending — schema must pass before service wires)
-- Owner: Kasra
--
-- Creates the identity spine that SSO (migration 024) and DISP-001 depend on.
-- principals maps 1:1 with a human or agent identity across tenants.
-- roles / role_permissions / role_assignments implement §1A role registry.

-- ── Principals ─────────────────────────────────────────────────────────────────
-- Stable identity record; created on first SSO login (JIT) or explicit provision.
CREATE TABLE IF NOT EXISTS principals (
    id              TEXT PRIMARY KEY,                    -- stable UUID-like or human slug
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    email           TEXT,                               -- may be null for non-human principals
    display_name    TEXT,
    principal_type  TEXT NOT NULL DEFAULT 'human'
                        CHECK (principal_type IN ('human', 'agent', 'service')),
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suspended', 'deprovisioned')),
    mfa_required    BOOLEAN NOT NULL DEFAULT false,     -- set by role policy, not stored here
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_principals_tenant     ON principals (tenant_id);
CREATE INDEX IF NOT EXISTS idx_principals_email      ON principals (tenant_id, email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_principals_status     ON principals (status) WHERE status = 'active';

-- ── Role Registry (§1A DDL) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roles (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    description TEXT,
    mfa_required BOOLEAN NOT NULL DEFAULT false,    -- if true, MFA challenge required
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_roles_tenant_project ON roles (tenant_id, project_id);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id    TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission TEXT NOT NULL,
    PRIMARY KEY (role_id, permission)
);

CREATE TABLE IF NOT EXISTS role_assignments (
    role_id       TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    assignee_id   TEXT NOT NULL,
    assignee_type TEXT NOT NULL DEFAULT 'agent'
                      CHECK (assignee_type IN ('agent', 'human', 'service')),
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by   TEXT NOT NULL,
    PRIMARY KEY (role_id, assignee_id)
);

CREATE INDEX IF NOT EXISTS idx_role_assignments_assignee ON role_assignments (assignee_id);

-- ── Seed roles (tenant_id = 'default', project_id = 'sos') ────────────────────
-- These are the canonical SOS roles every deployment starts with.
INSERT INTO roles (id, project_id, tenant_id, name, description)
VALUES
    ('role:sos:principal',   'sos', 'default', 'principal',   'Core SOS identity — founding agents and humans'),
    ('role:sos:coordinator', 'sos', 'default', 'coordinator', 'Protocol coordinator — owns governance and task dispatch'),
    ('role:sos:builder',     'sos', 'default', 'builder',     'Implementation agent — builds features, runs tests'),
    ('role:sos:gate',        'sos', 'default', 'gate',        'Quality gate — approves or rejects build artifacts'),
    ('role:sos:knight',      'sos', 'default', 'knight',      'Customer agent — serves a single tenant/project'),
    ('role:sos:worker',      'sos', 'default', 'worker',      'Ephemeral task runner — stateless, low-trust'),
    ('role:sos:partner',     'sos', 'default', 'partner',     'External partner — limited read + referral access'),
    ('role:sos:customer',    'sos', 'default', 'customer',    'End customer — access to their own data only'),
    ('role:sos:observer',    'sos', 'default', 'observer',    'Read-only across public and project tiers')
ON CONFLICT (project_id, name, tenant_id) DO NOTHING;
