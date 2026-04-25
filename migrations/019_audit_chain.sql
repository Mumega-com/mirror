-- Migration 019: unified hash-chained audit stream + WORM anchor support (Burst 2B-2)
-- Absorbs Sprint 001 K9/K10 (immutable lock + hash-chained timestamps).
-- Owner: Athena (schema) · Kasra (emission, anchor job, verify_chain utility)
-- G4 implementation notes baked in:
--   - payload JSONB capped at 8KB at emission (enforced in application layer, documented here)
--   - seq uses PostgreSQL sequence per stream_id (see audit_emit() helper below)
--   - Ed25519 signature mandatory on dispatcher stream, optional elsewhere

CREATE TABLE IF NOT EXISTS audit_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id   TEXT NOT NULL,          -- 'kernel' | 'mirror' | 'squad' | 'dispatcher' | 'plugin:<name>'
    seq         BIGINT NOT NULL,        -- monotonic within stream_id, from per-stream PG sequence
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id    TEXT NOT NULL,          -- principal_id or 'system'
    actor_type  TEXT NOT NULL
                    CHECK (actor_type IN ('agent','human','system')),
    action      TEXT NOT NULL,          -- verb: 'created','updated','deleted','granted','denied','minted',...
    resource    TEXT NOT NULL,          -- e.g. 'engram:abc123', 'role_assignment:xyz'
    payload     JSONB,                  -- redacted snapshot; MUST be ≤8KB at emission (app-enforced)
    payload_redacted BOOLEAN NOT NULL DEFAULT false,  -- true when payload was replaced with {summary, hash_of_full}
    prev_hash   BYTEA,                  -- NULL for genesis event of each stream
    hash        BYTEA NOT NULL,         -- SHA-256(prev_hash_bytes || canonical_json(event minus hash))
    signature   BYTEA,                  -- Ed25519(hash); mandatory for stream_id='dispatcher', optional elsewhere
    UNIQUE (stream_id, seq)
);

-- Partition-friendly indexes
CREATE INDEX IF NOT EXISTS idx_audit_events_stream_seq
    ON audit_events (stream_id, seq);

CREATE INDEX IF NOT EXISTS idx_audit_events_ts
    ON audit_events (ts DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_actor
    ON audit_events (actor_id, stream_id);

CREATE INDEX IF NOT EXISTS idx_audit_events_resource
    ON audit_events (resource);

-- GIN index for cross-stream payload queries (ISO 42001 forensics)
CREATE INDEX IF NOT EXISTS idx_audit_events_payload
    ON audit_events USING GIN (payload);


-- Per-stream sequence registry (avoids concurrent seq races)
-- One row per stream_id; seq increments atomically via nextval-equivalent advisory lock pattern.
-- Application calls audit_next_seq(stream_id) before inserting an event.
CREATE TABLE IF NOT EXISTS audit_stream_seqs (
    stream_id   TEXT PRIMARY KEY,
    last_seq    BIGINT NOT NULL DEFAULT 0,
    genesis_hash BYTEA           -- hash of stream's first event; NULL until first event written
);

-- Atomic sequence increment function (advisory lock on stream_id hash)
CREATE OR REPLACE FUNCTION audit_next_seq(p_stream_id TEXT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE
    v_seq BIGINT;
    v_lock_key BIGINT;
BEGIN
    -- Advisory lock key = abs(hashtext(stream_id)) to avoid global lock
    v_lock_key := abs(hashtext(p_stream_id));
    PERFORM pg_advisory_xact_lock(v_lock_key);

    INSERT INTO audit_stream_seqs (stream_id, last_seq)
    VALUES (p_stream_id, 1)
    ON CONFLICT (stream_id) DO UPDATE
        SET last_seq = audit_stream_seqs.last_seq + 1
    RETURNING last_seq INTO v_seq;

    RETURN v_seq;
END;
$$;


-- WORM anchor table — records chain-head state written to R2 every 15 minutes
CREATE TABLE IF NOT EXISTS audit_anchors (
    id              BIGSERIAL PRIMARY KEY,
    stream_id       TEXT NOT NULL,
    anchored_seq    BIGINT NOT NULL,
    chain_head_hash BYTEA NOT NULL,
    prev_anchor_hash BYTEA,             -- links anchors into their own chain
    anchor_hash     BYTEA NOT NULL,     -- SHA-256 of this anchor row (for R2 object name)
    r2_object_key   TEXT,               -- 'anchors/{yyyy}/{mm}/{dd}/{stream_id}-{seq}.json'
    anchored_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (stream_id, anchored_seq)
);

CREATE INDEX IF NOT EXISTS idx_audit_anchors_stream
    ON audit_anchors (stream_id, anchored_at DESC);
