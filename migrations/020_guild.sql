-- Migration 020: §13 Guild — durable organization primitive (Sprint 003 Track C)
-- Gate: Athena G8 APPROVED (spec v1.1)
-- Owner: Athena (schema) · Kasra (contract + backfill)
-- Constitutional: tier=entity with entity_id=guild.id; requires CallerContext.guildIds[]
--   extension in hive-access.ts (spec §6). audit_events preserved by §2B.2 immutability
--   on cascade delete (governance_log is operational; audit chain is the immutable record).

CREATE TABLE IF NOT EXISTS guilds (
    id                  TEXT PRIMARY KEY,               -- slug, e.g. 'mumega-inc'
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('company','project','community','meta-guild')),
    parent_guild_id     TEXT REFERENCES guilds(id) ON DELETE SET NULL,
    founded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    charter_doc_node_id TEXT REFERENCES docs_nodes(id) ON DELETE SET NULL,
    governance_tier     TEXT NOT NULL DEFAULT 'principal-only'
                        CHECK (governance_tier IN ('principal-only','consensus','delegated','automated')),
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','dormant','dissolved')),
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT guilds_no_self_parent CHECK (parent_guild_id IS NULL OR parent_guild_id != id)
);

CREATE INDEX IF NOT EXISTS idx_guilds_kind   ON guilds(kind);
CREATE INDEX IF NOT EXISTS idx_guilds_parent ON guilds(parent_guild_id);
CREATE INDEX IF NOT EXISTS idx_guilds_status ON guilds(status);

CREATE OR REPLACE FUNCTION guilds_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_guilds_updated_at
    BEFORE UPDATE ON guilds
    FOR EACH ROW EXECUTE FUNCTION guilds_touch_updated_at();


CREATE TABLE IF NOT EXISTS guild_members (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    member_type TEXT NOT NULL CHECK (member_type IN ('human','agent','squad')),
    member_id   TEXT NOT NULL,              -- profile_id (humans/agents) or squad_id
    rank        TEXT NOT NULL,              -- free text per guild: 'founder','builder','advisor','observer'
    scopes      JSONB,                      -- per-action permissions within guild
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    left_at     TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','suspended','left','removed')),
    UNIQUE (guild_id, member_type, member_id)
    -- Re-adds: use ON CONFLICT (guild_id, member_type, member_id) DO UPDATE SET status='active'
    -- Circular hierarchy (A→B→A) not enforced at DB level in v1; application must guard.
);

CREATE INDEX IF NOT EXISTS idx_guild_members_guild  ON guild_members(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_guild_members_member ON guild_members(member_type, member_id, status);


CREATE TABLE IF NOT EXISTS guild_treasuries (
    id              BIGSERIAL PRIMARY KEY,
    guild_id        TEXT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    currency        TEXT NOT NULL,          -- 'USD','CAD','MIND','BTC'
    balance         NUMERIC(18,4) NOT NULL DEFAULT 0
                    CHECK (balance >= 0),
    frozen_balance  NUMERIC(18,4) NOT NULL DEFAULT 0
                    CHECK (frozen_balance >= 0),
    last_settled_at TIMESTAMPTZ,
    UNIQUE (guild_id, currency)
);

CREATE INDEX IF NOT EXISTS idx_guild_treasuries_guild ON guild_treasuries(guild_id);


CREATE TABLE IF NOT EXISTS guild_governance_log (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    action      TEXT NOT NULL CHECK (action IN (
                    'member_added','rank_changed','treasury_debited','treasury_credited',
                    'charter_amended','status_changed','dissolution_initiated','dissolution_finalized'
                )),
    decided_by  TEXT NOT NULL,              -- profile_id of decision-maker
    ratified_by TEXT[],                     -- profile_ids of ratifiers (consensus mode)
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence_ref TEXT,                      -- audit_events.id, docs_nodes.id, etc.
    payload     JSONB
);

CREATE INDEX IF NOT EXISTS idx_guild_gov_log_guild ON guild_governance_log(guild_id, decided_at DESC);
