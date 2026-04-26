-- Migration 046: knight_discord_bindings — Sprint 008 S008-A / G76
-- Gate: Kasra
--
-- Stores the 1:1 mapping between a minted knight and its Discord channel.
-- V1: single binding per knight (knight_id PK enforces).

CREATE TABLE IF NOT EXISTS knight_discord_bindings (
    knight_id          TEXT        PRIMARY KEY REFERENCES principals(id) ON DELETE RESTRICT,
    discord_channel_id TEXT        NOT NULL,
    bound_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    bound_by           TEXT        NOT NULL  -- signer: "loom" or "hadi"
);

CREATE INDEX IF NOT EXISTS idx_knight_discord_bindings_channel
    ON knight_discord_bindings (discord_channel_id);

COMMENT ON TABLE knight_discord_bindings IS
    'Knight-to-Discord-channel binding. V1: 1:1 mapping. '
    'Sprint 008 S008-A/G76. knight_id is PK (one channel per knight).';
