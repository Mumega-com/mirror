"""Unit tests for kernel.outbox — MemoryOutbox + factory + drain loop.

S024 Track F Phase 2 LOCK F-16.

NativeSqlOutbox path is exercised by the mechanical check (kill Inkwell,
ingest, observe pending grow + drain on restore) — it requires postgres
and is documented in the F-16 README. These tests cover the in-process
contract and the failure paths the drain loop must handle.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from kernel.outbox import (
    BACKOFF_SCHEDULE_SEC,
    DEFAULT_MAX_ATTEMPTS,
    MemoryOutbox,
    OutboxBackend,
    OutboxRow,
    is_outbox_enabled,
    make_outbox,
)


# ---------------------------------------------------------------------------
# is_outbox_enabled
# ---------------------------------------------------------------------------

def test_outbox_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MIRROR_OUTBOX_ENABLED", raising=False)
    assert is_outbox_enabled() is False


@pytest.mark.parametrize("flag", ["1", "true", "True"])
def test_outbox_enabled_truthy_values(monkeypatch, flag):
    monkeypatch.setenv("MIRROR_OUTBOX_ENABLED", flag)
    assert is_outbox_enabled() is True


# ---------------------------------------------------------------------------
# MemoryOutbox — enqueue / claim / confirm
# ---------------------------------------------------------------------------

def test_enqueue_returns_monotonic_ids():
    ob = MemoryOutbox()
    a = ob.enqueue(None, {"k": 1})
    b = ob.enqueue(None, {"k": 2})
    assert b == a + 1


def test_claim_returns_oldest_pending():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "first"})
    ob.enqueue(None, {"k": "second"})
    row = ob.claim()
    assert row is not None
    assert row.payload == {"k": "first"}
    assert row.attempt_count == 1


def test_claim_skips_in_flight_rows():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    first = ob.claim()  # marks in_flight
    second = ob.claim()
    assert first is not None
    assert second is None  # only one row, already claimed


def test_confirm_deletes_row():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    ob.confirm(row.id)
    assert ob.stats() == {"pending": 0, "in_flight": 0, "dlq": 0}


# ---------------------------------------------------------------------------
# Release / DLQ semantics
# ---------------------------------------------------------------------------

def test_release_pushes_visible_after_into_future():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    ob.release(row.id, error="boom", attempt_count=row.attempt_count)
    # Immediately re-claiming returns None — row is hidden behind backoff.
    assert ob.claim() is None
    assert ob.stats()["pending"] == 1


def test_release_then_visible_after_backoff(monkeypatch):
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    ob.release(row.id, error="boom", attempt_count=row.attempt_count)

    # Fast-forward time by patching MemoryOutbox's notion of "now" via
    # the visible_after stored on the row directly. We monkey the row to
    # be visible.
    ob._rows[row.id]["visible_after"] = time.time() - 1
    second = ob.claim()
    assert second is not None
    assert second.id == row.id
    assert second.attempt_count == 2


def test_dlq_excludes_from_claim():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    ob.dlq(row.id, error="poisoned")
    assert ob.claim() is None
    assert ob.stats()["dlq"] == 1


def test_backoff_schedule_caps_at_tail():
    # After exhausting the schedule, idx = len-1 should still apply.
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    high_attempts = len(BACKOFF_SCHEDULE_SEC) + 100
    ob.release(row.id, error="x", attempt_count=high_attempts)
    # Should not raise IndexError — that's the contract.
    assert ob.stats()["pending"] == 1


# ---------------------------------------------------------------------------
# dlq_count / dlq_inspect — F-16 ABC additions per brief §6.6
# ---------------------------------------------------------------------------

def test_dlq_count_zero_when_no_dlq_rows():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    assert ob.dlq_count() == 0


def test_dlq_count_matches_stats_dlq():
    ob = MemoryOutbox()
    for i in range(3):
        ob.enqueue(None, {"k": i})
    for _ in range(3):
        row = ob.claim()
        ob.dlq(row.id, error="poisoned")
    assert ob.dlq_count() == 3
    assert ob.dlq_count() == ob.stats()["dlq"]


def test_dlq_count_respects_queue_param():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1}, queue="q-a")
    ob.enqueue(None, {"k": 2}, queue="q-b")
    a = ob.claim(queue="q-a")
    b = ob.claim(queue="q-b")
    ob.dlq(a.id, error="boom-a")
    ob.dlq(b.id, error="boom-b")
    assert ob.dlq_count(queue="q-a") == 1
    assert ob.dlq_count(queue="q-b") == 1


def test_dlq_inspect_returns_only_dlq_rows():
    ob = MemoryOutbox()
    ob.enqueue(None, {"alive": True})
    ob.enqueue(None, {"poisoned": True})
    keep = ob.claim()  # alive — leaves it claimed but not dlq'd
    ob.confirm(keep.id)
    second = ob.enqueue(None, {"poisoned-2": True})  # noqa: F841
    bad = ob.claim()
    ob.dlq(bad.id, error="boom")
    rows = ob.dlq_inspect()
    assert len(rows) == 1
    assert rows[0]["last_error"] == "boom"
    assert rows[0]["payload"] == {"poisoned": True}


def test_dlq_inspect_orders_newest_first_and_caps_limit():
    ob = MemoryOutbox()
    ids = []
    for i in range(5):
        ob.enqueue(None, {"i": i})
        row = ob.claim()
        ob.dlq(row.id, error=f"boom-{i}")
        ids.append(row.id)
    rows = ob.dlq_inspect(limit=3)
    assert len(rows) == 3
    # Newest first → last enqueued id should lead.
    assert rows[0]["id"] == ids[-1]
    assert [r["id"] for r in rows] == list(reversed(ids))[:3]


def test_dlq_inspect_payload_is_copy_not_alias():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "v"})
    row = ob.claim()
    ob.dlq(row.id, error="boom")
    out = ob.dlq_inspect()
    out[0]["payload"]["mutated"] = True
    # Internal row must not have been mutated through the returned dict.
    again = ob.dlq_inspect()
    assert "mutated" not in again[0]["payload"]


# ---------------------------------------------------------------------------
# reclaim_stuck_in_flight — adversarial-gate BLOCK-P0-1 closure
# ---------------------------------------------------------------------------

def test_reclaim_returns_zero_when_no_in_flight_rows():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    assert ob.reclaim_stuck_in_flight(stale_seconds=1) == 0


def test_reclaim_skips_freshly_in_flight_rows():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    ob.claim()  # marks in_flight, _in_flight_at = now
    # Fresh claim — should not be reclaimed.
    assert ob.reclaim_stuck_in_flight(stale_seconds=300) == 0
    assert ob.stats()["in_flight"] == 1


def test_reclaim_pulls_stuck_rows_back_to_pending():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    # Force the in_flight timestamp into the past — simulates drain SIGKILL.
    ob._rows[row.id]["_in_flight_at"] = time.time() - 10_000
    n = ob.reclaim_stuck_in_flight(stale_seconds=300)
    assert n == 1
    assert ob.stats()["pending"] == 1
    assert ob.stats()["in_flight"] == 0
    # Reclaimed row must be claimable on the next cycle.
    second = ob.claim()
    assert second is not None
    assert second.id == row.id
    # last_error annotated so operators can spot reclaimed rows.
    assert "reclaimed-stuck-in-flight" in (ob._rows[row.id]["last_error"] or "")


def test_reclaim_does_not_touch_dlq_rows():
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": 1})
    row = ob.claim()
    ob.dlq(row.id, error="poisoned")
    # Even if we forge an old in_flight timestamp, dlq state is sticky.
    ob._rows[row.id]["_in_flight_at"] = time.time() - 10_000
    assert ob.reclaim_stuck_in_flight(stale_seconds=1) == 0
    assert ob.stats()["dlq"] == 1


# ---------------------------------------------------------------------------
# is_durable + factory require_durable — adversarial-gate BLOCK-P1-7
# ---------------------------------------------------------------------------

def test_memory_outbox_reports_not_durable():
    ob = MemoryOutbox()
    assert ob.is_durable is False


def test_factory_refuses_memory_when_require_durable():
    # MIRROR_OUTBOX_BACKEND=memory should be REJECTED if caller demands durability.
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setenv("MIRROR_OUTBOX_BACKEND", "memory")
        with _pytest.raises(RuntimeError, match="not durable"):
            make_outbox(_StubLocalDB(), require_durable=True)


def test_factory_refuses_sqlite_db_when_require_durable():
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="not durable"):
        make_outbox(_StubSQLiteDB(), require_durable=True)


def test_factory_returns_native_when_require_durable_and_postgres():
    from kernel.outbox import NativeSqlOutbox
    ob = make_outbox(_StubLocalDB(), require_durable=True)
    assert isinstance(ob, NativeSqlOutbox)


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------

class _StubLocalDB:
    """Looks like LocalDB for the make_outbox check."""
    def __init__(self):
        self._pool = object()
    def _conn(self):
        raise NotImplementedError


class _StubSQLiteDB:
    """No _pool — should fall back to MemoryOutbox."""


def test_make_outbox_picks_native_for_postgres_db():
    from kernel.outbox import NativeSqlOutbox
    ob = make_outbox(_StubLocalDB())
    assert isinstance(ob, NativeSqlOutbox)


def test_make_outbox_falls_back_to_memory_for_other_backends():
    ob = make_outbox(_StubSQLiteDB())
    assert isinstance(ob, MemoryOutbox)


def test_make_outbox_env_override_forces_memory(monkeypatch):
    monkeypatch.setenv("MIRROR_OUTBOX_BACKEND", "memory")
    ob = make_outbox(_StubLocalDB())
    assert isinstance(ob, MemoryOutbox)


# ---------------------------------------------------------------------------
# Drain loop integration — uses MemoryOutbox + a fake post function
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.config = type("C", (), {
            "endpoint_url": "http://test",
            "token": "t",
            "principal": "p",
            "timeout_seconds": 1,
        })()

    def next_result(self):
        return self._results.pop(0) if self._results else (True, 200, "")


def _patch_post(monkeypatch, client):
    from kernel import outbox_drain as drain_mod

    def fake_post(_client, payload):
        client.calls.append(payload)
        return client.next_result()

    monkeypatch.setattr(drain_mod, "_post_with_status", fake_post)


def _stop_after(n_cycles, drain_mod):
    """Make the loop exit after n claim cycles (idle counts as a cycle)."""
    counter = {"n": 0}
    orig_sleep = drain_mod.time.sleep

    def fake_sleep(_secs):
        counter["n"] += 1
        if counter["n"] >= n_cycles:
            drain_mod._should_stop = True

    drain_mod.time.sleep = fake_sleep
    return lambda: setattr(drain_mod.time, "sleep", orig_sleep)


def test_drain_confirms_on_2xx(monkeypatch):
    from kernel import outbox_drain as drain_mod

    drain_mod._should_stop = False
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "ok"})
    client = _FakeClient([(True, 200, "")])
    _patch_post(monkeypatch, client)
    restore = _stop_after(2, drain_mod)
    try:
        drain_mod.run_drain_loop(ob, client)  # type: ignore[arg-type]
    finally:
        restore()
    assert ob.stats() == {"pending": 0, "in_flight": 0, "dlq": 0}
    assert client.calls == [{"k": "ok"}]


def test_drain_dlqs_on_non_retryable_4xx(monkeypatch):
    from kernel import outbox_drain as drain_mod

    drain_mod._should_stop = False
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "bad"})
    client = _FakeClient([(False, 422, "shape")])
    _patch_post(monkeypatch, client)
    restore = _stop_after(2, drain_mod)
    try:
        drain_mod.run_drain_loop(ob, client)  # type: ignore[arg-type]
    finally:
        restore()
    assert ob.stats() == {"pending": 0, "in_flight": 0, "dlq": 1}


def test_drain_releases_on_5xx_then_dlqs_when_exhausted(monkeypatch):
    from kernel import outbox_drain as drain_mod

    drain_mod._should_stop = False
    ob = MemoryOutbox()
    # Enqueue with low max_attempts so we exhaust quickly.
    ob.enqueue(None, {"k": "flaky"}, max_attempts=2)
    # Two 503s → first releases, second exhausts → dlq.
    client = _FakeClient([(False, 503, "down"), (False, 503, "still down")])
    _patch_post(monkeypatch, client)

    # Force the just-released row to be visible immediately for the
    # second cycle (we don't want to actually wait BACKOFF_SCHEDULE_SEC).
    orig_release = ob.release
    def patched_release(row_id, *, error, attempt_count):
        orig_release(row_id, error=error, attempt_count=attempt_count)
        ob._rows[row_id]["visible_after"] = time.time() - 1
    ob.release = patched_release  # type: ignore[method-assign]

    restore = _stop_after(3, drain_mod)
    try:
        drain_mod.run_drain_loop(ob, client)  # type: ignore[arg-type]
    finally:
        restore()
    stats = ob.stats()
    assert stats["dlq"] == 1
    assert stats["pending"] == 0


# ---------------------------------------------------------------------------
# Adversarial-gate hardening — drain non-retryable shape, startup reclaim
# ---------------------------------------------------------------------------

def test_non_retryable_set_excludes_token_and_deploy_codes():
    """BLOCK-P1-3 + BLOCK-P1-4: 401/404/410 must NOT be permanent-DLQ.

    401 → token rotation transient.
    404/410 → Inkwell deploy/route-reshuffle window.
    """
    from kernel.outbox_drain import NON_RETRYABLE_HTTP_STATUS
    assert 401 not in NON_RETRYABLE_HTTP_STATUS
    assert 404 not in NON_RETRYABLE_HTTP_STATUS
    assert 410 not in NON_RETRYABLE_HTTP_STATUS
    # 400/403/422 ARE structural — payload/auth shape is wrong.
    assert 400 in NON_RETRYABLE_HTTP_STATUS
    assert 403 in NON_RETRYABLE_HTTP_STATUS
    assert 422 in NON_RETRYABLE_HTTP_STATUS


def test_drain_releases_on_401_not_dlq(monkeypatch):
    """401 must release-with-backoff (token rotation), NOT permanent-DLQ.

    BLOCK-P1-4: a single 401 used to permanently DLQ every queued row.
    """
    from kernel import outbox_drain as drain_mod

    drain_mod._should_stop = False
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "rotating"}, max_attempts=8)
    client = _FakeClient([(False, 401, "stale token")])
    _patch_post(monkeypatch, client)
    restore = _stop_after(2, drain_mod)
    try:
        drain_mod.run_drain_loop(ob, client)
    finally:
        restore()
    # Row is released back to pending, NOT in dlq.
    stats = ob.stats()
    assert stats["dlq"] == 0
    assert stats["pending"] == 1


def test_drain_releases_on_404_not_dlq(monkeypatch):
    """404 must release-with-backoff (deploy window), NOT permanent-DLQ.

    BLOCK-P1-3: Inkwell deploy/route-reshuffle returning 404 used to
    permanently DLQ the entire pending queue.
    """
    from kernel import outbox_drain as drain_mod

    drain_mod._should_stop = False
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "deploying"}, max_attempts=8)
    client = _FakeClient([(False, 404, "no route")])
    _patch_post(monkeypatch, client)
    restore = _stop_after(2, drain_mod)
    try:
        drain_mod.run_drain_loop(ob, client)
    finally:
        restore()
    stats = ob.stats()
    assert stats["dlq"] == 0
    assert stats["pending"] == 1


def test_drain_startup_reclaims_stuck_in_flight(monkeypatch):
    """BLOCK-P0-1: drain startup pulls stuck in_flight rows back to pending.

    A previous drain SIGKILL'd between claim() and the terminal call would
    pin the row forever; the startup reclaim is the canon-target closure.
    """
    from kernel import outbox_drain as drain_mod

    drain_mod._should_stop = False
    ob = MemoryOutbox()
    ob.enqueue(None, {"k": "stranded"})
    row = ob.claim()
    # Forge a stale in_flight timestamp — simulates SIGKILL.
    ob._rows[row.id]["_in_flight_at"] = time.time() - 10_000
    # Drain should reclaim then claim+confirm on the next cycle.
    client = _FakeClient([(True, 200, "")])
    _patch_post(monkeypatch, client)
    restore = _stop_after(3, drain_mod)
    try:
        drain_mod.run_drain_loop(ob, client)
    finally:
        restore()
    # Row was reclaimed, then delivered, then confirmed → empty queue.
    assert ob.stats() == {"pending": 0, "in_flight": 0, "dlq": 0}
    assert client.calls == [{"k": "stranded"}]
