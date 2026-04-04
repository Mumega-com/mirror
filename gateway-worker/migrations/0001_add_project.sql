-- Add project column to engrams table
ALTER TABLE engrams ADD COLUMN project TEXT;
CREATE INDEX IF NOT EXISTS idx_engrams_project ON engrams(project);
CREATE INDEX IF NOT EXISTS idx_engrams_agent_project ON engrams(agent, project);
