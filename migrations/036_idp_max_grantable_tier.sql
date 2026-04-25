-- Migration 036: IdP tier ceiling column (Sprint 005 P0-5, Gate SCIM)
-- Owner: Kasra
-- Fixes F-10 HIGH: SCIM cross-tenant escalation via caller-supplied tenant_id.
-- Fixes F-15 HIGH: No ceiling on roles grantable via group → role maps.
--
-- Attack vectors:
--   F-10: scim_provision_user() accepted tenant_id from caller.
--         A bad actor with SCIM credentials for tenant A could pass tenant_id='default'
--         and provision principals into a different tenant.
--
--   F-15: add_group_role_map() accepted arbitrary role_ids with no tier check.
--         An IdP admin mapping group→role:sos:principal could elevate any SSO user
--         to platform-principal tier on login.
--
-- Fixes:
--   Code (sso.py):
--     F-10: scim_provision_user() now derives tenant_id from get_idp(idp_id).tenant_id.
--           Caller-supplied tenant_id parameter removed from the function signature.
--     F-15: add_group_role_map() now enforces idp.max_grantable_tier ceiling.
--           _complete_sso_login() and scim_provision_user() filter role_ids before assign.
--
--   DB (this migration):
--     New column idp_configurations.max_grantable_tier.
--     CHECK constraint prevents storing invalid tier names.
--     DEFAULT 'worker' — safe: allows basic provisioning, blocks elevated roles.

ALTER TABLE idp_configurations
    ADD COLUMN IF NOT EXISTS max_grantable_tier TEXT NOT NULL DEFAULT 'worker'
    CHECK (max_grantable_tier IN (
        'observer', 'customer', 'partner', 'worker',
        'knight', 'builder', 'gate', 'coordinator', 'principal'
    ));

-- Optional index if queries filter by tier (low cardinality, partial useful)
-- Not created by default — add in a later migration if query patterns require it.

COMMENT ON COLUMN idp_configurations.max_grantable_tier IS
    'F-15: Maximum role tier this IdP is allowed to grant via group mappings. '
    'Roles above this tier are silently dropped on login and SCIM provisioning. '
    'Tier order: observer < customer < partner < worker < knight < builder < gate < coordinator < principal.';
