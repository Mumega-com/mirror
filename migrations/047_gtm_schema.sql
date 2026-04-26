-- Migration 047: gtm schema — Sprint 008 S008-D / G79
-- Gate: Kasra
-- Embedding dimension: 768 (Gemini text-embedding, matches mirror engram embeddings)
--
-- Unified relationship graph for GTM substrate.
-- Source of truth for people, companies, deals, conversations, edges, actions.

CREATE SCHEMA IF NOT EXISTS gtm;

-- People: humans (contacts, prospects, customers, sales reps)
CREATE TABLE IF NOT EXISTS gtm.people (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL,
    email        TEXT,
    phone        TEXT,
    source       TEXT        NOT NULL CHECK (source IN ('ghl', 'discord', 'manual')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    deleted_at   TIMESTAMPTZ,
    embedding    VECTOR(768)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gtm_people_email
    ON gtm.people (email) WHERE email IS NOT NULL AND deleted_at IS NULL;

-- Companies: organizations
CREATE TABLE IF NOT EXISTS gtm.companies (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT        NOT NULL,
    domain     TEXT,
    industry   TEXT,
    source     TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    embedding  VECTOR(768)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gtm_companies_domain
    ON gtm.companies (domain) WHERE domain IS NOT NULL AND deleted_at IS NULL;

-- Deals: active sales opportunities
CREATE TABLE IF NOT EXISTS gtm.deals (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id         UUID        REFERENCES gtm.people(id) ON DELETE SET NULL,
    company_id        UUID        REFERENCES gtm.companies(id) ON DELETE SET NULL,
    product           TEXT        NOT NULL CHECK (product IN ('gaf', 'mumega', 'agentlink', 'other')),
    stage             TEXT        NOT NULL,
    value_cents       BIGINT,
    owner_knight_id   TEXT        REFERENCES principals(id) ON DELETE SET NULL,
    ghl_opportunity_id TEXT       UNIQUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_action_at    TIMESTAMPTZ,
    deleted_at        TIMESTAMPTZ,
    embedding         VECTOR(768)
);

-- Conversations: records of human↔human interactions
CREATE TABLE IF NOT EXISTS gtm.conversations (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    channel           TEXT        NOT NULL CHECK (channel IN ('discord', 'email', 'call', 'meeting', 'manual')),
    participants      JSONB       NOT NULL,
    summary           TEXT,
    transcript_url    TEXT,
    discord_message_id TEXT       UNIQUE,
    occurred_at       TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Edges: relationships between entities
CREATE TABLE IF NOT EXISTS gtm.edges (
    id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    from_id   UUID        NOT NULL,
    from_type TEXT        NOT NULL CHECK (from_type IN ('person', 'company', 'deal', 'knight')),
    to_id     UUID        NOT NULL,
    to_type   TEXT        NOT NULL CHECK (to_type IN ('person', 'company', 'deal', 'knight')),
    edge_type TEXT        NOT NULL,
    weight    FLOAT       DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_id, to_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_gtm_edges_from ON gtm.edges (from_id);
CREATE INDEX IF NOT EXISTS idx_gtm_edges_to ON gtm.edges (to_id);

-- Actions: things knight wants its rep to do
CREATE TABLE IF NOT EXISTS gtm.actions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    knight_id   TEXT        NOT NULL,
    action_type TEXT        NOT NULL,
    target_id   UUID,
    target_type TEXT,
    status      TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'done', 'skipped', 'failed')),
    due_at      TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB
);

CREATE INDEX IF NOT EXISTS idx_gtm_actions_knight ON gtm.actions (knight_id, status);
CREATE INDEX IF NOT EXISTS idx_gtm_actions_due ON gtm.actions (due_at) WHERE status = 'pending';

COMMENT ON SCHEMA gtm IS
    'GTM relationship graph — Sprint 008 S008-D/G79. Unified data substrate for '
    'people, companies, deals, conversations, edges, and knight-assigned actions.';
