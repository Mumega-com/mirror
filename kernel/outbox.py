"""Mirror outbox — durable staging for receipt emission.

S024 Track F Phase 2 LOCK F-16.

Closes the silent-fail-open canon target documented in
`kernel/receipts.py` L67 (`InkwellReceiptClient.append` swallows every
exception and returns None). Instead of POSTing to Inkwell inside the
caller's request, the caller ENQUEUEs an opaque payload to
`mirror_pending_receipts` in the SAME transaction as the underlying
business write. A separate drain worker (`kernel/outbox_drain.py`)
claims rows via SKIP LOCKED, POSTs them, and confirms / releases / dlqs.

Design choices:

- **ABC + factory.** `OutboxBackend` is the contract; `NativeSqlOutbox`
  is the postgres-backed prod path; `MemoryOutbox` is the test double.
  `make_outbox(db)` selects based on env (`MIRROR_OUTBOX_BACKEND`) +
  whether the underlying DB exposes a postgres `_conn()` pool.

- **Atomic enqueue.** `enqueue(conn, payload)` takes an EXTERNAL
  connection. The caller (`LocalDB.upsert_engram_with_outbox`) opens
  one transaction, inserts the engram + the pending receipt, commits.
  Either both land or neither.

- **SKIP LOCKED claim.** `claim()` does
  `SELECT ... WHERE state='pending' AND visible_after <= now()
   FOR UPDATE SKIP LOCKED` so concurrent drain workers never double-deliver.

- **State machine.** pending → in_flight → (back to pending with
  visible_after pushed out) on transient failure, → dlq when
  attempt_count >= max_attempts. Confirmation deletes the row.

- **No payload schema in code.** Payload is opaque JSONB; receipt shape
  versions independently of the table.
"""
from __future__ import annotations

import abc
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("mirror.outbox")

DEFAULT_QUEUE = "inkwell-receipts"
# 24 attempts on the schedule below puts last-DLQ at ~50h wall-clock —
# wide enough that a multi-hour Inkwell outage doesn't silently DLQ the
# pending backlog before operators see the F-17 alert thresholds breach.
# Adversarial-gate hardening BLOCK-P1-8 (was 8 = ~3.7h tail).
DEFAULT_MAX_ATTEMPTS = int(os.getenv("MIRROR_OUTBOX_MAX_ATTEMPTS", "24"))
# Backoff schedule (seconds) per attempt. Index = attempt_count BEFORE the
# release. Tail value applies to all further attempts.
BACKOFF_SCHEDULE_SEC = [5, 15, 60, 300, 900, 1800, 3600, 7200]


def is_outbox_enabled() -> bool:
    """Feature flag — controls whether callers route through the outbox.

    Default OFF until migration 052 is applied in prod (sequencing per
    S024 v0.5 §6.7: F-16 build now, deploy after F-13 GREEN). Turn on
    with `MIRROR_OUTBOX_ENABLED=1` once `mirror_pending_receipts` exists.
    """
    return os.getenv("MIRROR_OUTBOX_ENABLED", "0") in ("1", "true", "True")


@dataclass(frozen=True)
class OutboxRow:
    id: int
    queue_name: str
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int


class OutboxBackend(abc.ABC):
    """Contract for outbox storage.

    `enqueue` runs INSIDE the caller's transaction (takes a connection).
    `claim`/`confirm`/`release`/`dlq` are owned by the drain worker and
    each open their own transaction.
    """

    @abc.abstractmethod
    def enqueue(
        self,
        conn: Any,
        payload: dict[str, Any],
        *,
        queue: str = DEFAULT_QUEUE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> int:
        """Insert a pending row using *conn*. Returns the row id.

        Caller owns commit/rollback of *conn*. Receipt write is atomic
        with the business write iff caller binds them in one txn.
        """

    @abc.abstractmethod
    def claim(self, *, queue: str = DEFAULT_QUEUE) -> OutboxRow | None:
        """Atomically claim one visible pending row, mark it in_flight.

        Returns None when the queue is empty or no rows are visible.
        """

    @abc.abstractmethod
    def confirm(self, row_id: int) -> None:
        """Delete a successfully-delivered row."""

    @abc.abstractmethod
    def release(self, row_id: int, *, error: str, attempt_count: int) -> None:
        """Push the row back to pending with a backoff visible_after."""

    @abc.abstractmethod
    def dlq(self, row_id: int, *, error: str) -> None:
        """Mark the row as dlq — drain worker will not pick it up again."""

    @abc.abstractmethod
    def stats(self, *, queue: str = DEFAULT_QUEUE) -> dict[str, int]:
        """Return {pending, in_flight, dlq} counts. Powers F-17."""

    @abc.abstractmethod
    def dlq_count(self, *, queue: str = DEFAULT_QUEUE) -> int:
        """Return number of rows currently in dlq. (Brief §6.6 F-16 method.)"""

    @abc.abstractmethod
    def dlq_inspect(
        self, *, queue: str = DEFAULT_QUEUE, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* dlq rows for operator inspection.

        Each row: {id, queue_name, payload, attempt_count, last_error,
        updated_at}. (Brief §6.6 F-16 method.)
        """

    def reclaim_stuck_in_flight(
        self, *, queue: str = DEFAULT_QUEUE, stale_seconds: int = 300,
    ) -> int:
        """Reclaim rows pinned in_flight past *stale_seconds* back to pending.

        Closes BLOCK-P0-1 (drain-process SIGKILL between claim and the
        terminal confirm/release/dlq call would otherwise pin the row in
        in_flight forever, breaking the silent-fail-open canon target).

        Default impl is a no-op for backends where this is not meaningful
        (MemoryOutbox in-process, SQLite). NativeSqlOutbox overrides.

        Returns: number of rows reclaimed.
        """
        return 0

    @property
    def is_durable(self) -> bool:
        """Whether this backend survives process restart.

        Used by the factory to refuse a non-durable backend when the
        caller (`upsert_engram_with_outbox`) binds it inside the same
        transaction as a durable engram write. Subclass overrides.
        """
        return False


# ---------------------------------------------------------------------------
# NativeSqlOutbox — psycopg2 postgres backend (prod path)
# ---------------------------------------------------------------------------


class NativeSqlOutbox(OutboxBackend):
    """Postgres-backed outbox using the LocalDB connection pool.

    The pool is borrowed (not stored as a member) for claim/confirm/release/
    dlq operations. `enqueue` takes an EXTERNAL connection so the caller
    can bind it to the business-write transaction.
    """

    def __init__(self, local_db: Any) -> None:
        # Keep a reference to LocalDB so we can borrow connections for
        # drain-side ops without building a second pool.
        self._db = local_db

    # --- enqueue path: caller-owned txn -----------------------------------

    def enqueue(
        self,
        conn: Any,
        payload: dict[str, Any],
        *,
        queue: str = DEFAULT_QUEUE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_pending_receipts
                    (queue_name, payload, max_attempts)
                VALUES (%s, %s::jsonb, %s)
                RETURNING id
                """,
                [queue, json.dumps(payload), max_attempts],
            )
            row = cur.fetchone()
            return int(row[0])

    # --- drain-side ops: own txn each ------------------------------------

    def claim(self, *, queue: str = DEFAULT_QUEUE) -> OutboxRow | None:
        # autocommit=True is the LocalDB default; we wrap claim in a
        # short explicit transaction for the FOR UPDATE lock semantics.
        conn = self._db._pool.getconn()
        try:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, queue_name, payload, attempt_count, max_attempts
                          FROM mirror_pending_receipts
                         WHERE queue_name = %s
                           AND state = 'pending'
                           AND visible_after <= now()
                         ORDER BY id
                         FOR UPDATE SKIP LOCKED
                         LIMIT 1
                        """,
                        [queue],
                    )
                    row = cur.fetchone()
                    if row is None:
                        conn.commit()
                        return None
                    row_id = int(row[0])
                    cur.execute(
                        """
                        UPDATE mirror_pending_receipts
                           SET state         = 'in_flight',
                               attempt_count = attempt_count + 1,
                               updated_at    = now()
                         WHERE id = %s
                        """,
                        [row_id],
                    )
                conn.commit()
                payload = row[2] if isinstance(row[2], dict) else json.loads(row[2])
                return OutboxRow(
                    id=row_id,
                    queue_name=row[1],
                    payload=payload,
                    attempt_count=int(row[3]) + 1,  # post-claim count
                    max_attempts=int(row[4]),
                )
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = True
        finally:
            self._db._pool.putconn(conn)

    def confirm(self, row_id: int) -> None:
        with self._db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM mirror_pending_receipts WHERE id = %s",
                    [row_id],
                )

    def release(self, row_id: int, *, error: str, attempt_count: int) -> None:
        idx = min(attempt_count, len(BACKOFF_SCHEDULE_SEC) - 1)
        backoff_sec = BACKOFF_SCHEDULE_SEC[idx]
        with self._db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mirror_pending_receipts
                       SET state         = 'pending',
                           visible_after = now() + (%s || ' seconds')::interval,
                           last_error    = %s,
                           updated_at    = now()
                     WHERE id = %s
                    """,
                    [backoff_sec, error[:1000], row_id],
                )

    def dlq(self, row_id: int, *, error: str) -> None:
        with self._db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mirror_pending_receipts
                       SET state      = 'dlq',
                           last_error = %s,
                           updated_at = now()
                     WHERE id = %s
                    """,
                    [error[:1000], row_id],
                )

    def stats(self, *, queue: str = DEFAULT_QUEUE) -> dict[str, int]:
        with self._db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT state, COUNT(*)
                      FROM mirror_pending_receipts
                     WHERE queue_name = %s
                     GROUP BY state
                    """,
                    [queue],
                )
                rows = cur.fetchall()
        out = {"pending": 0, "in_flight": 0, "dlq": 0}
        for state, count in rows:
            out[state] = int(count)
        return out

    def dlq_count(self, *, queue: str = DEFAULT_QUEUE) -> int:
        return int(self.stats(queue=queue).get("dlq", 0))

    def dlq_inspect(
        self, *, queue: str = DEFAULT_QUEUE, limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, queue_name, payload, attempt_count,
                           last_error, updated_at
                      FROM mirror_pending_receipts
                     WHERE queue_name = %s AND state = 'dlq'
                     ORDER BY updated_at DESC
                     LIMIT %s
                    """,
                    [queue, max(1, min(int(limit), 100))],
                )
                rows = cur.fetchall()
        out = []
        for row in rows:
            payload = row[2] if isinstance(row[2], dict) else json.loads(row[2])
            out.append({
                "id": int(row[0]),
                "queue_name": row[1],
                "payload": payload,
                "attempt_count": int(row[3]),
                "last_error": row[4],
                "updated_at": row[5].isoformat() if row[5] else None,
            })
        return out

    def reclaim_stuck_in_flight(
        self, *, queue: str = DEFAULT_QUEUE, stale_seconds: int = 300,
    ) -> int:
        """Pull rows pinned in_flight past *stale_seconds* back to pending.

        Adversarial-gate hardening (BLOCK-P0-1). Drain SIGKILL between
        claim and confirm/release/dlq pins a row in_flight forever; the
        only claim filter is `state='pending'`, so without this sweeper
        the row silently never delivers AND never DLQs — a direct breach
        of F-16's silent-fail-open canon target.
        """
        seconds = max(1, int(stale_seconds))
        with self._db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mirror_pending_receipts
                       SET state = 'pending',
                           updated_at = now(),
                           last_error = COALESCE(last_error, '') ||
                                        ' [reclaimed-stuck-in-flight]'
                     WHERE queue_name = %s
                       AND state = 'in_flight'
                       AND updated_at < now() - (%s || ' seconds')::interval
                    """,
                    [queue, seconds],
                )
                return cur.rowcount or 0

    @property
    def is_durable(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# MemoryOutbox — in-process backend for tests (no DB required)
# ---------------------------------------------------------------------------


class MemoryOutbox(OutboxBackend):
    """In-memory outbox — used by tests and the SQLite backend.

    Concurrency-safe via a single mutex. `enqueue` ignores the *conn*
    arg (no transaction binding); for SQLite-backend deployments this is
    acceptable because SQLite's value-prop is dev/edge, not the audit
    contract that F-16 protects.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._rows: dict[int, dict[str, Any]] = {}

    def enqueue(
        self,
        conn: Any,
        payload: dict[str, Any],
        *,
        queue: str = DEFAULT_QUEUE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> int:
        with self._lock:
            row_id = self._next_id
            self._next_id += 1
            self._rows[row_id] = {
                "id": row_id,
                "queue_name": queue,
                "payload": dict(payload),
                "state": "pending",
                "attempt_count": 0,
                "max_attempts": max_attempts,
                "visible_after": time.time(),
                "last_error": None,
            }
            return row_id

    def claim(self, *, queue: str = DEFAULT_QUEUE) -> OutboxRow | None:
        with self._lock:
            now = time.time()
            for row_id in sorted(self._rows.keys()):
                row = self._rows[row_id]
                if (
                    row["queue_name"] == queue
                    and row["state"] == "pending"
                    and row["visible_after"] <= now
                ):
                    row["state"] = "in_flight"
                    row["attempt_count"] += 1
                    # _in_flight_at powers reclaim_stuck_in_flight (BLOCK-P0-1).
                    row["_in_flight_at"] = now
                    return OutboxRow(
                        id=row["id"],
                        queue_name=row["queue_name"],
                        payload=dict(row["payload"]),
                        attempt_count=row["attempt_count"],
                        max_attempts=row["max_attempts"],
                    )
            return None

    def confirm(self, row_id: int) -> None:
        with self._lock:
            self._rows.pop(row_id, None)

    def release(self, row_id: int, *, error: str, attempt_count: int) -> None:
        with self._lock:
            row = self._rows.get(row_id)
            if row is None:
                return
            idx = min(attempt_count, len(BACKOFF_SCHEDULE_SEC) - 1)
            row["state"] = "pending"
            row["visible_after"] = time.time() + BACKOFF_SCHEDULE_SEC[idx]
            row["last_error"] = error[:1000]

    def dlq(self, row_id: int, *, error: str) -> None:
        with self._lock:
            row = self._rows.get(row_id)
            if row is None:
                return
            row["state"] = "dlq"
            row["last_error"] = error[:1000]

    def stats(self, *, queue: str = DEFAULT_QUEUE) -> dict[str, int]:
        with self._lock:
            out = {"pending": 0, "in_flight": 0, "dlq": 0}
            for row in self._rows.values():
                if row["queue_name"] != queue:
                    continue
                out[row["state"]] += 1
            return out

    def dlq_count(self, *, queue: str = DEFAULT_QUEUE) -> int:
        return self.stats(queue=queue).get("dlq", 0)

    def dlq_inspect(
        self, *, queue: str = DEFAULT_QUEUE, limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                row for row in self._rows.values()
                if row["queue_name"] == queue and row["state"] == "dlq"
            ]
            rows.sort(key=lambda r: r["id"], reverse=True)
            out = []
            for row in rows[: max(1, min(int(limit), 100))]:
                out.append({
                    "id": row["id"],
                    "queue_name": row["queue_name"],
                    "payload": dict(row["payload"]),
                    "attempt_count": row["attempt_count"],
                    "last_error": row["last_error"],
                    "updated_at": None,
                })
            return out

    def reclaim_stuck_in_flight(
        self, *, queue: str = DEFAULT_QUEUE, stale_seconds: int = 300,
    ) -> int:
        """Pull rows pinned in_flight past *stale_seconds* back to pending."""
        cutoff = time.time() - max(1, int(stale_seconds))
        n = 0
        with self._lock:
            for row in self._rows.values():
                if (
                    row["queue_name"] == queue
                    and row["state"] == "in_flight"
                    and row.get("_in_flight_at", 0) < cutoff
                ):
                    row["state"] = "pending"
                    # Make immediately visible — drain just reclaimed it.
                    row["visible_after"] = time.time()
                    row["last_error"] = (
                        (row.get("last_error") or "") + " [reclaimed-stuck-in-flight]"
                    )
                    n += 1
        return n


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_outbox(db: Any, *, require_durable: bool = False) -> OutboxBackend:
    """Pick the right backend for the given DB.

    Selection order:
      1. `MIRROR_OUTBOX_BACKEND=memory` — force MemoryOutbox (test/dev).
      2. DB exposes `_pool` (LocalDB / postgres) — NativeSqlOutbox.
      3. Otherwise (SQLiteDB, supabase) — MemoryOutbox.

    The MemoryOutbox path on non-postgres backends is intentionally
    best-effort: SQLite + Supabase are not the prod audit-write surface,
    so the outbox contract is upheld but not durable across restarts.
    Operators running those backends in prod should turn the flag off.
    """
    if os.getenv("MIRROR_OUTBOX_BACKEND", "").lower() == "memory":
        ob: OutboxBackend = MemoryOutbox()
    elif hasattr(db, "_pool") and hasattr(db, "_conn"):
        ob = NativeSqlOutbox(db)
    else:
        ob = MemoryOutbox()
    # Adversarial-gate hardening (BLOCK-P1-7): the atomic-txn caller
    # (`upsert_engram_with_outbox`) MUST get a durable backend, otherwise
    # an env-var typo (`MIRROR_OUTBOX_BACKEND=memory` on a postgres deploy)
    # would silently let engram writes commit while the outbox row vanishes
    # on the next restart. Refuse to hand a non-durable backend to a caller
    # that asked for durability.
    if require_durable and not ob.is_durable:
        raise RuntimeError(
            "make_outbox(require_durable=True) refused: backend "
            f"{type(ob).__name__} is not durable. Check "
            "MIRROR_OUTBOX_BACKEND env or DB type."
        )
    return ob
