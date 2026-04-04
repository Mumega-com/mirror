-- SOS Mirror D1 Schema
-- Engram storage for AI agent memories

CREATE TABLE IF NOT EXISTS engrams (
  id TEXT PRIMARY KEY,
  context_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  series TEXT NOT NULL,
  project TEXT,
  text TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  metadata TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_engrams_agent ON engrams(agent);
CREATE INDEX IF NOT EXISTS idx_engrams_context ON engrams(context_id);
CREATE INDEX IF NOT EXISTS idx_engrams_timestamp ON engrams(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_engrams_series ON engrams(series);
CREATE INDEX IF NOT EXISTS idx_engrams_project ON engrams(project);
CREATE INDEX IF NOT EXISTS idx_engrams_agent_project ON engrams(agent, project);

-- Agent registry (persists registrations)
CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  model TEXT NOT NULL,
  roles TEXT,
  capabilities TEXT,
  namespace TEXT NOT NULL,
  api_key TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  last_seen TEXT
);

CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
CREATE INDEX IF NOT EXISTS idx_agents_namespace ON agents(namespace);
