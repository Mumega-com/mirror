# Mirror Local Memory Pilot Design

## Decision

Extend Mirror with a supported single-user local profile instead of operating
Mem0, Letta, Cognee, or Graphiti alongside it. The pilot remains non-load-bearing:
private GitHub documents and source systems remain authoritative until the pilot
passes durability, recovery, provenance, and retrieval-quality gates.

## Goals

- Persist Hadi CC memories locally in one SQLite database.
- Preserve Mirror's existing engram schema, provenance fields, tiers, and MCP
  `remember`, `recall`, and `recent` interface.
- Make memory writes and their receipt records survive process restarts.
- Support keyword retrieval without an embedding provider and hybrid retrieval
  when local embeddings are configured.
- Compare Mirror retrieval against a fixed baseline dataset before integrating
  it into Codex startup.

## Non-goals

- No production or VPS deployment.
- No global Codex MCP configuration or automatic startup hook.
- No ingestion of raw transcripts, secrets, credentials, or unrestricted files.
- No replacement of GitHub, Mupot, SOS, or Inkwell responsibilities.
- No Mem0 service installation during this pilot.

## Considered Approaches

### 1. Supported Mirror local profile — selected

Use Mirror's SQLite and sqlite-vec backend, add a durable SQLite outbox, and
exercise the existing MCP surface. This preserves one memory model and requires
the least new infrastructure.

### 2. Mem0 side-by-side benchmark service

Mem0 provides a polished self-hosted API and audit dashboard, but it introduces
another data model, extraction policy, vector store, migration path, and access
surface. It may be used later as an isolated benchmark implementation, not as a
second source of truth.

### 3. Graph-first replacement

Cognee or Graphiti could model temporal relationships, but their graph and LLM
extraction dependencies are unnecessary for the initial Hadi CC wake briefing.
Relationship extraction can be reconsidered after the simpler retrieval pilot
has measured gaps.

## Architecture

The local profile uses one database at an operator-selected path outside the
repository. `SQLiteDB` remains the memory store. A new SQLite-backed outbox uses
the same database connection and transaction as the engram write so the memory
and pending receipt either commit together or both roll back. The outbox drain
continues through the existing `OutboxBackend` contract.

Recall uses Mirror's current hybrid search path: vector candidates and SQLite
FTS5/BM25 candidates are fused with reciprocal-rank fusion. With no usable local
embedding provider, keyword recall must continue to work and must not fabricate
semantic results.

The MCP interface remains unchanged. The pilot is invoked explicitly from a
terminal or test client; it is not registered globally in Codex.

## Durable SQLite Outbox

The database contains a dedicated receipt table with these persistent fields:

- receipt identifier and queue name
- event kind and serialized payload
- state: `pending`, `in_flight`, or `dlq`
- attempt count and next-attempt timestamp
- created, updated, and visibility timestamps
- last error text

Required behavior:

1. An engram write with receipts enabled inserts the engram and pending receipt
   in one SQLite transaction.
2. Restarting the process preserves pending and dead-letter receipts.
3. Claiming work is atomic and does not return the same receipt concurrently.
4. Delivery success removes the confirmed receipt, matching the established
   `OutboxBackend` contract. Retryable failure increments attempts and schedules
   the next attempt; exhausted failure moves it to `dlq`.
5. `make_outbox(require_durable=True)` selects the SQLite implementation for
   `SQLiteDB` and continues refusing the in-memory implementation.
6. Existing Postgres behavior and explicit test/dev memory-outbox selection do
   not change.

## Pilot Dataset and Benchmark

Create a synthetic, non-sensitive fixture of 30 to 50 Hadi CC-style memories.
Every record includes a source reference, project, agent, timestamp, and one of
these types: decision, blocker, next action, status, or preference. The fixture
contains deliberate near-duplicates and superseded facts.

The benchmark runs a fixed query set covering exact names, project aliases,
recent decisions, owners, blockers, and superseded facts. It records:

- whether the expected record appears in the top five
- reciprocal rank of the expected record
- query latency
- whether the returned result exposes its source and synthesized status
- whether a no-answer query correctly returns no confident memory

The comparison is FTS5-only versus Mirror hybrid retrieval. A Mem0 comparison is
deferred unless Mirror fails the retrieval gate.

## Acceptance Gates

- The complete existing Mirror test suite passes on the canonical public-main
  history.
- New SQLite outbox tests demonstrate red-before-green behavior and persistence
  across independent database instances.
- A forced transaction failure leaves neither an engram nor a receipt.
- Keyword recall works with embeddings disabled.
- At least 90 percent of positive benchmark queries return the expected record
  in the top five, and all no-answer queries avoid fabricated matches.
- Every benchmark result retains source and synthesized/provenance fields.
- Export and restore of the local SQLite database reproduce the same record
  counts and benchmark answers.

## Safety and Rollback

The pilot database contains synthetic or explicitly approved non-sensitive
records only. It is stored outside Git and is never committed. Automatic capture
is disabled. Removing the pilot configuration and database fully rolls back the
experiment; no production service, token, route, or Codex-wide configuration is
changed.

## Follow-up Decision

Only after the acceptance gates pass should Hadi decide whether to connect the
local Mirror MCP to the Hadi-assistant startup workflow. If the retrieval gate
fails, run the same fixture against Mem0 as a benchmark before deciding whether
to improve or replace Mirror.
