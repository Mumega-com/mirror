# Mirror Local Memory Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Mirror's single-user SQLite profile durable, searchable without a hosted embedding provider, and measurable with a deterministic local-memory benchmark.

**Architecture:** Keep the existing `SQLiteDB`, MCP tools, engram schema, provenance firewall, and `OutboxBackend` interface. Add a persistent `SQLiteOutbox` using the same SQLite transaction as the engram write, an FTS5 index synchronized inside that transaction, and an offline benchmark runner using synthetic Hadi CC-style records. Do not install Mem0 or connect Mirror globally during this pilot.

**Tech Stack:** Python 3.11+, SQLite/WAL/FTS5, sqlite-vec, pytest, existing Mirror MCP and outbox interfaces.

## Global Constraints

- No production or VPS deployment.
- No global Codex MCP configuration or automatic startup hook.
- No raw transcripts, secrets, credentials, customer data, or unrestricted files in fixtures.
- Private GitHub documents and owning source systems remain authoritative.
- Automatic capture stays disabled.
- Existing Postgres outbox behavior and MCP tool signatures must not change.
- Every production-code behavior change follows red-green-refactor.

---

## File Map

- `kernel/outbox.py`: implement the persistent `SQLiteOutbox` and select it from `make_outbox`.
- `kernel/db_sqlite.py`: create SQLite outbox/FTS schemas, centralize transactional engram upsert, expose atomic outbox writes, BM25 search, and SQLite backup.
- `tests/test_sqlite_outbox.py`: prove durability, state transitions, factory selection, and atomic rollback.
- `tests/test_sqlite_fts.py`: prove FTS synchronization, workspace filtering, updates, and no-answer behavior.
- `benchmarks/local_memory_fixture.json`: synthetic, source-labelled memory corpus and expected query outcomes.
- `benchmarks/run_local_memory_pilot.py`: load the fixture, run FTS/hybrid queries, calculate gates, and verify backup/restore.
- `tests/test_local_memory_pilot.py`: test benchmark calculations and a complete offline passing run.
- `docs/local-memory-pilot.md`: operator commands, safety boundary, outputs, and rollback.

---

### Task 1: Persistent SQLite Outbox Backend

**Files:**
- Modify: `kernel/db_sqlite.py`
- Modify: `kernel/outbox.py`
- Create: `tests/test_sqlite_outbox.py`

**Interfaces:**
- Consumes: `SQLiteDB._conn()`, `OutboxBackend`, `OutboxRow`, `BACKOFF_SCHEDULE_SEC`, `DEFAULT_QUEUE`, and `DEFAULT_MAX_ATTEMPTS`.
- Produces: `SQLiteOutbox(db: SQLiteDB)`, persistent `mirror_pending_receipts`, and factory selection through `make_outbox(db, require_durable=False) -> OutboxBackend`.

- [ ] **Step 1: Write failing persistence and factory tests**

Create `tests/test_sqlite_outbox.py` with real temporary SQLite databases:

```python
from __future__ import annotations

import time

from kernel.db_sqlite import SQLiteDB
from kernel.outbox import SQLiteOutbox, make_outbox


def test_sqlite_outbox_survives_new_backend_instance(tmp_path):
    db_path = tmp_path / "mirror.db"
    db = SQLiteDB(db_path=str(db_path), dims=8)
    first = SQLiteOutbox(db)
    with db._conn() as conn:
        row_id = first.enqueue(conn, {"context_id": "ctx-1"})

    second = SQLiteOutbox(SQLiteDB(db_path=str(db_path), dims=8))
    claimed = second.claim()

    assert claimed is not None
    assert claimed.id == row_id
    assert claimed.payload == {"context_id": "ctx-1"}
    assert claimed.attempt_count == 1


def test_factory_selects_durable_sqlite_outbox(tmp_path):
    db = SQLiteDB(db_path=str(tmp_path / "mirror.db"), dims=8)
    outbox = make_outbox(db, require_durable=True)
    assert isinstance(outbox, SQLiteOutbox)
    assert outbox.is_durable is True
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_sqlite_outbox.py -q
```

Expected: collection fails because `SQLiteOutbox` does not exist.

- [ ] **Step 3: Add the persistent schema**

In `SQLiteDB._init_db`, create the table and queue/state index inside the existing transaction:

```sql
CREATE TABLE IF NOT EXISTS mirror_pending_receipts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_name    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending', 'in_flight', 'dlq')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL,
    visible_after REAL NOT NULL,
    last_error    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
)
```

Create `idx_pending_receipts_claim` on `(queue_name, state, visible_after, id)`.

- [ ] **Step 4: Implement `SQLiteOutbox`**

Add `SQLiteOutbox` to `kernel/outbox.py`. It must:

- serialize payloads with `json.dumps(..., sort_keys=True)`;
- use the caller-provided `sqlite3.Connection` in `enqueue`;
- claim one visible row with `BEGIN IMMEDIATE`, ordered by `id`, then set `state='in_flight'`, increment `attempt_count`, and update `updated_at` before commit;
- delete on `confirm`;
- set `pending`, `visible_after`, and truncated `last_error` on `release`;
- set `dlq` and truncated `last_error` on `dlq`;
- implement `stats`, `dlq_count`, `dlq_inspect`, and stale in-flight reclamation using the existing contract;
- return `True` from `is_durable`.

Use epoch seconds for `visible_after`, `created_at`, and `updated_at`, matching `time.time()` and avoiding SQLite timezone ambiguity.

- [ ] **Step 5: Update factory selection**

Import `SQLiteDB` lazily inside `make_outbox` and select in this order:

```python
if os.getenv("MIRROR_OUTBOX_BACKEND", "").lower() == "memory":
    ob = MemoryOutbox()
elif hasattr(db, "_pool") and hasattr(db, "_conn"):
    ob = NativeSqlOutbox(db)
else:
    from kernel.db_sqlite import SQLiteDB
    if isinstance(db, SQLiteDB):
        ob = SQLiteOutbox(db)
    else:
        ob = MemoryOutbox()
```

Preserve the final `require_durable` rejection.

- [ ] **Step 6: Add state-machine coverage**

Extend `tests/test_sqlite_outbox.py` with separate tests for confirm deletion, release/backoff, DLQ exclusion, queue filtering, inspection order, and reclaiming a forged stale `in_flight` row. Assert on public methods and direct SQL only when setting the stale timestamp.

- [ ] **Step 7: Run focused and existing outbox tests**

Run:

```bash
python3 -m pytest tests/test_sqlite_outbox.py tests/test_outbox.py -q
```

Expected: all tests pass, including unchanged `MemoryOutbox` and Postgres factory tests. Keep `_StubSQLiteDB` as the unknown-backend fallback test and use a real temporary `SQLiteDB` for the new SQLite factory assertion.

- [ ] **Step 8: Commit Task 1**

```bash
git add kernel/db_sqlite.py kernel/outbox.py tests/test_sqlite_outbox.py tests/test_outbox.py
git commit -m "feat(mirror): add durable SQLite receipt outbox"
```

---

### Task 2: Atomic SQLite Engram and Receipt Writes

**Files:**
- Modify: `kernel/db_sqlite.py`
- Modify: `tests/test_sqlite_outbox.py`

**Interfaces:**
- Consumes: `SQLiteOutbox.enqueue(conn, payload) -> int` and the existing engram schema.
- Produces: `SQLiteDB.upsert_engram_with_outbox(data: dict, receipt_payload: dict, outbox: OutboxBackend) -> int` and private `_upsert_engram_on_conn(conn: sqlite3.Connection, data: dict) -> dict`.

- [ ] **Step 1: Write failing atomicity tests**

Add these behaviors to `tests/test_sqlite_outbox.py`:

```python
def test_atomic_engram_and_receipt_commit(tmp_path):
    db = SQLiteDB(db_path=str(tmp_path / "mirror.db"), dims=8)
    outbox = SQLiteOutbox(db)
    row_id = db.upsert_engram_with_outbox(
        _engram("atomic-ok", [0.0] * 8),
        {"context_id": "atomic-ok"},
        outbox,
    )
    assert row_id > 0
    assert db.count_engrams() == 1
    assert outbox.stats()["pending"] == 1


def test_atomic_failure_rolls_back_engram_and_receipt(tmp_path):
    class InsertThenFail(SQLiteOutbox):
        def enqueue(self, conn, payload, **kwargs):
            super().enqueue(conn, payload, **kwargs)
            raise RuntimeError("forced enqueue failure")

    db = SQLiteDB(db_path=str(tmp_path / "mirror.db"), dims=8)
    outbox = InsertThenFail(db)
    with pytest.raises(RuntimeError, match="forced enqueue failure"):
        db.upsert_engram_with_outbox(
            _engram("atomic-fail", [0.0] * 8),
            {"context_id": "atomic-fail"},
            outbox,
        )
    assert db.count_engrams() == 0
    assert SQLiteOutbox(db).stats() == {"pending": 0, "in_flight": 0, "dlq": 0}
```

Define `_engram(context_id, embedding)` in the test with `workspace_id='hadi-cc'`, `raw_data={'text': context_id}`, and valid tier/provenance defaults.

- [ ] **Step 2: Run the atomic tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_sqlite_outbox.py -q
```

Expected: failure because `SQLiteDB.upsert_engram_with_outbox` is absent.

- [ ] **Step 3: Extract connection-scoped upsert**

Refactor `SQLiteDB.upsert_engram` without changing its public result:

```python
def upsert_engram(self, data: dict) -> dict:
    row = dict(data)
    with self._conn() as conn:
        return self._upsert_engram_on_conn(conn, row)

def _upsert_engram_on_conn(self, conn: sqlite3.Connection, data: dict) -> dict:
    row = dict(data)
    embedding = row.pop("embedding", None)
    context_id = row["context_id"]
    tier = row.get("tier", "project")
    if tier not in {"public", "squad", "project", "entity", "private"}:
        raise ValueError(f"Invalid tier: {tier!r}. Must be one of: public, squad, project, entity, private")
    # Execute the current parameterized mirror_engrams INSERT/ON CONFLICT,
    # resolve its id by context_id, then perform the current vec0 DELETE/INSERT.
    return {"context_id": row["context_id"], "status": "ok"}
```

Move the current parameter map and parameterized SQL from `upsert_engram` into
this helper verbatim; the only semantic change is that `row`, rather than the
caller's dictionary, loses its `embedding` key. Keep JSON serialization, ID
resolution, and sqlite-vec delete/insert behavior identical.

- [ ] **Step 4: Implement atomic outbox upsert**

Add:

```python
def upsert_engram_with_outbox(self, data, receipt_payload, outbox) -> int:
    with self._conn() as conn:
        self._upsert_engram_on_conn(conn, data)
        return outbox.enqueue(conn, receipt_payload)
```

The existing `_conn` rollback path must roll back both operations when either raises.

- [ ] **Step 5: Run atomicity and route tests**

Run:

```bash
python3 -m pytest tests/test_sqlite_outbox.py tests/test_memory_receipts.py tests/test_mcp_server.py -q
```

Expected: all pass, and the route/MCP outbox-enabled path no longer fails closed on SQLite.

- [ ] **Step 6: Commit Task 2**

```bash
git add kernel/db_sqlite.py tests/test_sqlite_outbox.py
git commit -m "feat(mirror): bind SQLite engrams and receipts atomically"
```

---

### Task 3: SQLite FTS5 and BM25 Recall

**Files:**
- Modify: `kernel/db_sqlite.py`
- Create: `tests/test_sqlite_fts.py`
- Modify: `tests/test_hybrid_search.py`

**Interfaces:**
- Consumes: `_upsert_engram_on_conn`, `SQLiteDB._row_to_engram`, and `kernel.search.hybrid_search`.
- Produces: `SQLiteDB.search_bm25(query: str, limit: int, workspace_id: str | None = None) -> list[dict]` and persistent `mirror_engrams_fts`.

- [ ] **Step 1: Write failing FTS tests**

Create tests using `SQLiteDB(db_path=..., dims=8)` and zero embeddings:

```python
def test_bm25_finds_exact_project_fact(tmp_path):
    db = _db(tmp_path)
    db.upsert_engram(_engram("kids-deadline", "Kidssentials application deadline is August 7"))
    rows = db.search_bm25("Kidssentials deadline", limit=5, workspace_id="hadi-cc")
    assert [row["context_id"] for row in rows] == ["kids-deadline"]
    assert rows[0]["synthesized"] is False
    assert rows[0]["raw_data"]["source"] == "fixture://kids-deadline"


def test_bm25_returns_empty_for_no_answer(tmp_path):
    db = _db(tmp_path)
    db.upsert_engram(_engram("known", "Mirror local pilot uses SQLite"))
    assert db.search_bm25("unicorn submarine payroll", 5, "hadi-cc") == []
```

Also test workspace exclusion and updating an existing `context_id` so removed terms no longer match and new terms do.

- [ ] **Step 2: Run the FTS tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_sqlite_fts.py -q
```

Expected: failure because SQLite lacks `search_bm25` and its FTS table.

- [ ] **Step 3: Create and synchronize the FTS5 table**

In `_init_db`, create a standalone content table so indexing remains explicit and transaction-bound:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS mirror_engrams_fts USING fts5(
    context_id UNINDEXED,
    text,
    epistemic_truths,
    core_concepts,
    project,
    series,
    tokenize='unicode61'
)
```

In `_upsert_engram_on_conn`, after resolving the engram ID:

1. delete the previous FTS row by `context_id`;
2. insert current text from `raw_data['text']`, joined epistemic truths and core concepts, project, and series.

This sync occurs in the same transaction as the engram and optional outbox row.

- [ ] **Step 4: Implement `search_bm25`**

Query `mirror_engrams_fts` joined to `mirror_engrams` by `context_id`, filter by workspace when supplied, exclude `importance_score < 0.1` and archived rows, order by SQLite `bm25(mirror_engrams_fts)` ascending, and limit with a positive integer capped at 100. Convert each joined engram through `_row_to_engram` and attach `bm25_rank` as a float.

An empty/whitespace query returns `[]`. Invalid FTS syntax must be converted to a quoted phrase query or return `[]`; it must not escape parameter binding or expose SQL errors through REST/MCP.

- [ ] **Step 5: Replace the obsolete hybrid fallback assertion**

Update `tests/test_hybrid_search.py` so it proves SQLite BM25 participates in RRF instead of asserting SQLite lacks BM25. Patch `db.search_engrams` to return no vector candidates, store an exact-text record, call `hybrid_search`, and assert the BM25 record is returned with provenance fields intact.

- [ ] **Step 6: Run recall tests**

```bash
python3 -m pytest tests/test_sqlite_fts.py tests/test_hybrid_search.py tests/test_workspace_isolation.py tests/test_engram_tiers.py -q
```

Expected: all pass with no cross-workspace results.

- [ ] **Step 7: Commit Task 3**

```bash
git add kernel/db_sqlite.py tests/test_sqlite_fts.py tests/test_hybrid_search.py
git commit -m "feat(mirror): add SQLite FTS5 recall"
```

---

### Task 4: Deterministic Pilot Benchmark and Backup/Restore

**Files:**
- Modify: `kernel/db_sqlite.py`
- Create: `benchmarks/local_memory_fixture.json`
- Create: `benchmarks/run_local_memory_pilot.py`
- Create: `tests/test_local_memory_pilot.py`

**Interfaces:**
- Consumes: `SQLiteDB.upsert_engram`, `SQLiteDB.search_bm25`, `kernel.search.hybrid_search`, and `kernel.embeddings.get_embedding`.
- Produces: `SQLiteDB.backup_to(destination: str) -> None`, `run_pilot(fixture_path: Path, db_path: Path, mode: str) -> dict`, and a JSON-serializable benchmark report.

- [ ] **Step 1: Write failing backup and metric tests**

In `tests/test_local_memory_pilot.py`, add:

```python
def test_sqlite_backup_restores_records(tmp_path):
    source = SQLiteDB(str(tmp_path / "source.db"), dims=8)
    source.upsert_engram(_engram("backup-one", "Backup preserves this memory"))
    source.backup_to(str(tmp_path / "restored.db"))
    restored = SQLiteDB(str(tmp_path / "restored.db"), dims=8)
    assert restored.count_engrams() == 1
    assert restored.search_bm25("Backup preserves", 5, "hadi-cc")[0]["context_id"] == "backup-one"


def test_metrics_require_top_five_and_reject_false_answers():
    report = score_queries([
        {"kind": "positive", "expected_context_id": "a", "returned": ["b", "a"]},
        {"kind": "no_answer", "expected_context_id": None, "returned": []},
    ])
    assert report["top5_rate"] == 1.0
    assert report["no_answer_pass_rate"] == 1.0
    assert report["passed"] is True
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m pytest tests/test_local_memory_pilot.py -q
```

Expected: failure because `backup_to`, `score_queries`, and the runner do not exist.

- [ ] **Step 3: Implement safe SQLite backup**

Use the standard SQLite backup API, not file copying:

```python
def backup_to(self, destination: str) -> None:
    destination_path = Path(destination).expanduser()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with self._conn() as source:
        target = sqlite3.connect(str(destination_path))
        try:
            source.backup(target)
        finally:
            target.close()
```

Reject a destination resolving to the active database path.

- [ ] **Step 4: Create the synthetic fixture**

Create 36 records across `hadi-cc`, `kids`, `mupot`, `sos`, `inkwell`, and `dme`. Every record includes:

```json
{
  "context_id": "hcc-decision-memory-stack",
  "workspace_id": "hadi-cc",
  "project": "hadi-cc",
  "agent": "hadi-assistant",
  "timestamp": "2026-08-05T05:00:00Z",
  "type": "decision",
  "text": "Private GitHub remains authoritative while Mirror local is a non-load-bearing recall pilot.",
  "source": "fixture://hcc-decision-memory-stack",
  "synthesized": false
}
```

Add 12 positive queries and 4 no-answer queries. Include exact names, aliases, blockers, owners, newer-vs-superseded facts, and near-duplicates. No record may contain real credentials, customer documents, email contents, phone numbers, or raw conversation text.

- [ ] **Step 5: Implement benchmark runner**

`run_pilot` must:

1. delete only the explicitly supplied temporary/pilot database path when `--reset` is passed;
2. load fixture records as project-tier engrams with source/type metadata;
3. run `fts` or `hybrid` mode using a fixed `top_k=5`;
4. record returned context IDs, reciprocal rank, elapsed milliseconds, source presence, and synthesized flag presence;
5. score positive and no-answer queries separately;
6. set `passed=true` only when positive top-five rate is at least `0.90`, no-answer pass rate is `1.0`, and provenance completeness is `1.0`;
7. back up the database, reopen the backup, rerun FTS queries, and assert identical record count and returned context IDs;
8. print JSON and exit `0` on pass or `1` on gate failure.

Expose:

```bash
python3 benchmarks/run_local_memory_pilot.py \
  --fixture benchmarks/local_memory_fixture.json \
  --db /tmp/mirror-local-pilot.db \
  --mode fts \
  --reset
```

Hybrid mode sets `MIRROR_EMBED_PROVIDER=local`, `MIRROR_EMBED_DIMS=384`, and
`MIRROR_VECTOR_DIMS=384` before lazily importing `kernel.embeddings`,
`kernel.search`, or `SQLiteDB`. It must never call a hosted provider.

- [ ] **Step 6: Test a complete offline pilot**

Add a test that calls `run_pilot` with the fixture and a temporary database in `fts` mode. Assert `record_count == 36`, `passed is True`, backup verification is true, and all report fields serialize with `json.dumps`.

- [ ] **Step 7: Run benchmark tests and both modes**

```bash
python3 -m pytest tests/test_local_memory_pilot.py -q
python3 benchmarks/run_local_memory_pilot.py --fixture benchmarks/local_memory_fixture.json --db /tmp/mirror-local-pilot-fts.db --mode fts --reset
python3 benchmarks/run_local_memory_pilot.py --fixture benchmarks/local_memory_fixture.json --db /tmp/mirror-local-pilot-hybrid.db --mode hybrid --reset
```

Expected: FTS mode passes the acceptance gate. Record hybrid results even if hybrid fails; do not weaken the fixture or thresholds to force a pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add kernel/db_sqlite.py benchmarks/local_memory_fixture.json benchmarks/run_local_memory_pilot.py tests/test_local_memory_pilot.py
git commit -m "test(mirror): add offline local-memory pilot"
```

---

### Task 5: Operator Documentation and Full Verification

**Files:**
- Create: `docs/local-memory-pilot.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the completed SQLite profile and benchmark CLI.
- Produces: a safe operator runbook; no runtime API changes.

- [ ] **Step 1: Write the runbook**

Document:

- prerequisites and a repository-local virtual environment;
- explicit environment variables for `MIRROR_BACKEND=sqlite`, an operator-selected `MIRROR_SQLITE_PATH`, `MIRROR_EMBED_PROVIDER=local`, and `MIRROR_OUTBOX_ENABLED=1`;
- the benchmark commands from Task 4;
- how to inspect outbox stats without displaying payloads;
- how to back up and restore through `SQLiteDB.backup_to`;
- the non-load-bearing status and prohibition on secrets/raw transcripts;
- rollback: stop the explicit pilot process and move the exact pilot database and backup to Trash; do not delete a broad directory;
- the later decision gate for a per-thread MCP connection.

- [ ] **Step 2: Update README local-profile guidance**

Add a short link to `docs/local-memory-pilot.md`. Correct the quick-start wording so PostgreSQL remains the multi-tenant deployment path while SQLite is documented as the supported single-user local pilot path.

- [ ] **Step 3: Run formatting and targeted verification**

```bash
git diff --check
python3 -m pytest tests/test_sqlite_outbox.py tests/test_sqlite_fts.py tests/test_local_memory_pilot.py tests/test_outbox.py tests/test_hybrid_search.py -q
```

Expected: no whitespace errors and all targeted tests pass.

- [ ] **Step 4: Run the complete suite**

```bash
python3 -m pytest -q
```

Expected: the complete suite passes. If environment-dependent Postgres tests skip, report the exact skipped count; do not claim those paths were executed.

- [ ] **Step 5: Run the final FTS pilot and preserve its report**

```bash
python3 benchmarks/run_local_memory_pilot.py \
  --fixture benchmarks/local_memory_fixture.json \
  --db /tmp/mirror-local-pilot-final.db \
  --mode fts \
  --reset > /tmp/mirror-local-pilot-final-report.json
```

Read the report, verify `passed=true`, and report its metrics. Do not commit the generated database, backup, or report.

- [ ] **Step 6: Commit Task 5**

```bash
git add README.md docs/local-memory-pilot.md
git commit -m "docs(mirror): document local memory pilot"
```

- [ ] **Step 7: Review the completed branch**

Use `superpowers:requesting-code-review`, resolve findings with `superpowers:receiving-code-review`, rerun the complete suite and final benchmark, then use `superpowers:finishing-a-development-branch` to present merge/publish options. Do not push or deploy without Hadi's explicit approval.
