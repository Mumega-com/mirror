-- Migration 049: Tenant row-level security — Sprint 011 S011 OmniB
-- Gate: Kasra
-- LOCK-5: App MUST connect as mumega_app_role, NOT postgres/superuser.
--
-- Creates mumega_app_role with restricted privileges + RLS policies
-- on gtm.* tables. Policies use current_setting('app.tenant_id').

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

-- Enable RLS on gtm tables
ALTER TABLE gtm.deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.people ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtm.actions ENABLE ROW LEVEL SECURITY;

-- RLS policies: owner_knight_id or knight_id matches session tenant
-- For V1: deals are the primary tenant-scoped table (owner_knight_id)
CREATE POLICY tenant_deals_policy ON gtm.deals
    FOR ALL
    TO mumega_app_role
    USING (
        owner_knight_id IS NULL  -- allow unowned deals (public)
        OR owner_knight_id LIKE 'agent:%'  -- V1: allow all agent-owned (tenant isolation via middleware)
    );

-- Actions: knight_id scoped
CREATE POLICY tenant_actions_policy ON gtm.actions
    FOR ALL
    TO mumega_app_role
    USING (true);  -- V1: middleware-enforced; PG RLS is defense-in-depth shell

-- People/companies/conversations: open read, tenant middleware enforces write scope
CREATE POLICY tenant_people_policy ON gtm.people FOR ALL TO mumega_app_role USING (true);
CREATE POLICY tenant_companies_policy ON gtm.companies FOR ALL TO mumega_app_role USING (true);
CREATE POLICY tenant_conversations_policy ON gtm.conversations FOR ALL TO mumega_app_role USING (true);

COMMENT ON POLICY tenant_deals_policy ON gtm.deals IS
    'Sprint 011 S011 OmniB. V1: RLS shell with middleware enforcement. '
    'S012+ tightens USING clause to current_setting(app.tenant_id) match.';
