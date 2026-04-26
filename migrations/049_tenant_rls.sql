-- Migration 049: Tenant row-level security — Sprint 011 S011 OmniB v0.2
-- Gate: Kasra
-- LOCK-5: App MUST connect as mumega_app_role, NOT postgres/superuser.
-- ADV-S011-003 fix: USING(true) = zero isolation. All policies must use
-- current_setting('app.tenant_id', true) for real tenant scoping.

-- Create restricted app role (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mumega_app_role') THEN
        CREATE ROLE mumega_app_role LOGIN;
    END IF;
END $$;

-- Grant schema usage
GRANT USAGE ON SCHEMA public TO mumega_app_role;
GRANT USAGE ON SCHEMA gtm TO mumega_app_role;

-- Grant table access (SELECT, INSERT, UPDATE — no DELETE, no TRUNCATE)
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA gtm TO mumega_app_role;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO mumega_app_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA gtm TO mumega_app_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO mumega_app_role;

-- Add tenant_id column to tables that lack it (for RLS scoping)
ALTER TABLE gtm.people ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'mumega';
ALTER TABLE gtm.companies ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'mumega';
ALTER TABLE gtm.conversations ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'mumega';

-- gtm.deals already has owner_knight_id → derive tenant via principals.tenant_id
-- For direct RLS: add tenant_id column to deals too
ALTER TABLE gtm.deals ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'mumega';

-- gtm.actions: add tenant_id
ALTER TABLE gtm.actions ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'mumega';

-- Enable RLS on gtm tables
ALTER TABLE gtm.deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.people ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.actions ENABLE ROW LEVEL SECURITY;

-- Drop old permissive policies if they exist
DROP POLICY IF EXISTS tenant_deals_policy ON gtm.deals;
DROP POLICY IF EXISTS tenant_actions_policy ON gtm.actions;
DROP POLICY IF EXISTS tenant_people_policy ON gtm.people;
DROP POLICY IF EXISTS tenant_companies_policy ON gtm.companies;
DROP POLICY IF EXISTS tenant_conversations_policy ON gtm.conversations;

-- ADV-S011-003 fix: real tenant isolation via current_setting('app.tenant_id')
-- App calls SET app.tenant_id = '<tenant>' per connection (tenant_rls.py:set_tenant_context)
CREATE POLICY tenant_deals_policy ON gtm.deals
    FOR ALL TO mumega_app_role
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY tenant_actions_policy ON gtm.actions
    FOR ALL TO mumega_app_role
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY tenant_people_policy ON gtm.people
    FOR ALL TO mumega_app_role
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY tenant_companies_policy ON gtm.companies
    FOR ALL TO mumega_app_role
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY tenant_conversations_policy ON gtm.conversations
    FOR ALL TO mumega_app_role
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Index on tenant_id for RLS performance
CREATE INDEX IF NOT EXISTS idx_gtm_deals_tenant ON gtm.deals (tenant_id);
CREATE INDEX IF NOT EXISTS idx_gtm_people_tenant ON gtm.people (tenant_id);
CREATE INDEX IF NOT EXISTS idx_gtm_companies_tenant ON gtm.companies (tenant_id);
CREATE INDEX IF NOT EXISTS idx_gtm_actions_tenant ON gtm.actions (tenant_id);

COMMENT ON POLICY tenant_deals_policy ON gtm.deals IS
    'Sprint 011 v0.2 ADV-S011-003: real tenant isolation via current_setting(app.tenant_id).';
