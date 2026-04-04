-- Migration 005: Budget Enforcement
-- Ported from Paperclip's budget model, adapted for Supabase PostgreSQL
-- Scope: agent / customer / project / global policies with soft+hard thresholds

-- Budget policies (per agent, per customer, per project)
CREATE TABLE IF NOT EXISTS budget_policies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type TEXT NOT NULL CHECK (scope_type IN ('agent', 'customer', 'project', 'global')),
  scope_id TEXT NOT NULL,
  metric TEXT NOT NULL DEFAULT 'cost_cents',
  window_kind TEXT NOT NULL DEFAULT 'calendar_month_utc',
  amount_cents INTEGER NOT NULL,
  warn_percent INTEGER DEFAULT 80,
  hard_stop BOOLEAN DEFAULT true,
  enabled BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Cost events (every model call logged)
CREATE TABLE IF NOT EXISTS cost_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id TEXT NOT NULL,
  customer_id TEXT,
  project TEXT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cost_cents INTEGER DEFAULT 0,
  run_id TEXT,
  occurred_at TIMESTAMPTZ DEFAULT now()
);

-- Budget incidents (when limits hit)
CREATE TABLE IF NOT EXISTS budget_incidents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  policy_id UUID REFERENCES budget_policies,
  threshold_type TEXT NOT NULL CHECK (threshold_type IN ('warning', 'hard_stop')),
  amount_limit INTEGER NOT NULL,
  amount_observed INTEGER NOT NULL,
  status TEXT DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
  resolved_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Activity log (audit trail)
CREATE TABLE IF NOT EXISTS activity_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_type TEXT NOT NULL CHECK (actor_type IN ('system', 'user', 'agent')),
  actor_id TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  details JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cost_events_agent ON cost_events(agent_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_cost_events_customer ON cost_events(customer_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_cost_events_project ON cost_events(project, occurred_at);
CREATE INDEX IF NOT EXISTS idx_budget_incidents_policy ON budget_incidents(policy_id, status);
CREATE INDEX IF NOT EXISTS idx_activity_log_actor ON activity_log(actor_type, actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_activity_log_entity ON activity_log(entity_type, entity_id, created_at);

-- RLS
ALTER TABLE budget_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_log ENABLE ROW LEVEL SECURITY;

-- Service role can do everything
CREATE POLICY "Service role full access" ON budget_policies FOR ALL USING (true);
CREATE POLICY "Service role full access" ON cost_events FOR ALL USING (true);
CREATE POLICY "Service role full access" ON budget_incidents FOR ALL USING (true);
CREATE POLICY "Service role full access" ON activity_log FOR ALL USING (true);

-- Seed default policies
INSERT INTO budget_policies (scope_type, scope_id, amount_cents, warn_percent) VALUES
('agent', 'athena', 50000, 80),   -- $500/month for Athena
('agent', 'kasra', 30000, 80),    -- $300/month for Kasra
('global', 'mumega', 200000, 80); -- $2000/month total
