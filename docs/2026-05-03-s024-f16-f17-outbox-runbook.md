# S024 Track F Phase 2 — F-16 + F-17 Outbox Runbook

**LOCK:** F-16 (Mirror outbox + drain) + F-17 (substrate-monitor `outbox.status` MCP).
**Brief:** v0.5 §6.6, §6.7.
**Status:** built + GREEN; awaiting paired Athena ratification.

## What this closes

The silent-fail-open canon target documented in `kernel/receipts.py` L67
(`InkwellReceiptClient.append` swallows every exception and returns
`None`). Before F-16, an Inkwell hiccup during ingestion meant the
engram was written to Mirror but the receipt was permanently lost — no
audit trail, no replay path. After F-16, the receipt payload is
enqueued to `mirror_pending_receipts` in the **same Postgres transaction**
as the engram write. Either both land or neither. A separate drain
worker POSTs the queued payloads to Inkwell with retries, backoff, and
DLQ for poison rows.

## Topology

```
ingest request ──► LocalDB.upsert_engram_with_outbox(engram, receipt_payload)
                     │
                     ├─ INSERT engram (ON CONFLICT update)        ┐ same txn
                     └─ INSERT mirror_pending_receipts (state=pending) ┘
                          │
                          ▼
                   commit ✓  (returns outbox_id)

mirror-outbox-drain.service (long-running)
   loop:
     row = SKIP LOCKED claim ──► POST inkwell  ──► 2xx  ──► confirm (delete)
                                              ──► 4xx structural ──► dlq
                                              ──► retryable / 5xx / net ──► release + backoff
                                              ──► attempts >= max ──► dlq
```

Substrate monitor (F-17) aggregates per-component state across Mirror,
SOS, and Inkwell-incoming via the `outbox_status` MCP tool. Mirror
reads come from `GET /admin/outbox/status` (admin-token gated).

## Files

| File | Role |
|---|---|
| `migrations/052_mirror_pending_receipts.sql` | outbox table + 3 partial indexes |
| `kernel/outbox.py` | `OutboxBackend` ABC + `NativeSqlOutbox` + `MemoryOutbox` + `make_outbox` factory |
| `kernel/db.py:upsert_engram_with_outbox` | atomic engram+enqueue txn |
| `kernel/outbox_drain.py` | long-running drain loop |
| `~/.config/systemd/user/mirror-outbox-drain.service` | drain unit (After=mirror.service) |
| `plugins/memory/routes.py` + `plugins/mcp_server/tools.py` | callers wired to feature flag |
| `plugins/admin/routes.py` | `/admin/outbox/status` + `/admin/outbox/dlq` |
| `tests/test_outbox.py` | 24 unit tests |
| `sos/mcp/sos_mcp_sse.py` | F-17 `outbox_status` MCP tool + aggregator |
| `tests/mcp/test_outbox_status.py` | 10 unit tests |

## Feature flags

| Env | Default | What it does |
|---|---|---|
| `MIRROR_OUTBOX_ENABLED` | unset (off) | Master switch. When off, callers fall back to fire-and-forget `db.upsert_engram` + `emit_mirror_engram_write_receipt`. |
| `MIRROR_OUTBOX_BACKEND` | unset (auto) | Set to `memory` to force MemoryOutbox even when DB is postgres (test/dev). |
| `MIRROR_OUTBOX_MAX_ATTEMPTS` | `24` | Max attempts before a row is moved to DLQ. (Was 8 pre-adversarial-gate; raised to give a ~50h tail so multi-hour Inkwell outages don't silently DLQ before the F-17 alert threshold (10) trips.) |
| `MIRROR_OUTBOX_STUCK_TIMEOUT_SEC` | `300` | Visibility timeout — rows pinned `in_flight` past this are reclaimed back to `pending` on drain startup AND every `MIRROR_OUTBOX_RECLAIM_EVERY` cycles. Closes BLOCK-P0-1 (drain SIGKILL between claim and confirm/release/dlq). |
| `MIRROR_OUTBOX_RECLAIM_EVERY` | `60` | Periodic-reclaim cadence (claim cycles between sweeps). |
| `MIRROR_OUTBOX_IDLE_SLEEP_SEC` | `2` | Drain idle sleep when claim returns None. |
| `MIRROR_OUTBOX_ERROR_SLEEP_SEC` | `5` | Drain sleep on unexpected exception in the loop. |
| `MIRROR_ADMIN_TOKEN` | unset | Bearer for `/admin/outbox/*`; SOS MCP F-17 also reads this. |

## NON_RETRYABLE_HTTP_STATUS

`{400, 403, 422}` only. Adversarial-gate hardening narrowed this set:

- **401 dropped** — token rotation is transient. Operator restarts drain;
  in-flight rows must NOT permanently DLQ during the rotation window.
- **404 dropped** — Inkwell deploy/route-reshuffle window can transient-404
  a freshly-cut Worker.
- **410 dropped** — same shape as 404; "permanently gone" vs "deploy in
  flight" is not safe to distinguish at the drain.

Operators replay DLQ manually if 401/403 turns out to be permanent.

## Authz on outbox_status MCP tool

The `outbox_status` MCP tool surfaces operator-only data (cross-substrate
DLQ depths + upstream `last_error` echoes from Inkwell). It is gated by
`STRICT_SYSTEM_ONLY_TOOLS` in `sos/mcp/sos_mcp_sse.py` — tenant tokens
are denied at dispatch, NOT just by convention.

## Atomic-txn caller durability

Callers of `LocalDB.upsert_engram_with_outbox` (the atomic-txn helper)
MUST call `make_outbox(db, require_durable=True)`. The factory raises
`RuntimeError` if `MIRROR_OUTBOX_BACKEND=memory` is set on a postgres
deploy AND the caller is the atomic helper — prevents silent durability
loss from an env-var typo.

## Backoff schedule

`BACKOFF_SCHEDULE_SEC = [5, 15, 60, 300, 900, 1800, 3600, 7200]` —
indexed by attempt_count, capped at the tail. After 8 attempts a row
moves to DLQ.

## Alert thresholds (F-17)

Per brief §6.6:

| Metric | Threshold |
|---|---|
| `dlq_count` | 10 |
| `pending_count` | 1000 |
| `stale_pending_seconds` | 3600 |

## Mechanical check (kill Inkwell, observe pending grow → drain on restore)

This is the F-16/F-17 acceptance check per brief §6.7. Run **only after**
F-12/F-13/F-14 (Codex) are Athena-ratified GREEN and the paired
F-16+F-17 ratification has cleared.

```bash
# 1. Apply migration to prod.
cd ~/mirror && psql "$DATABASE_URL" -f migrations/052_mirror_pending_receipts.sql

# 2. Flip the flag in the env file used by mirror.service + drain.
echo 'MIRROR_OUTBOX_ENABLED=1' >> ~/.env.secrets

# 3. Bring up the drain service and reload Mirror.
systemctl --user daemon-reload
systemctl --user enable --now mirror-outbox-drain.service
systemctl --user restart mirror.service

# 4. Verify the components branch reports backend=native.
curl -s -H "Authorization: Bearer $MIRROR_ADMIN_TOKEN" \
  http://localhost:8844/admin/outbox/status | jq

# 5. Severe Inkwell connectivity (firewall the receipts host or stop the
#    Worker route). Do this for ~5 minutes during active ingestion.
#    Watch pending grow:
watch -n 5 'curl -s -H "Authorization: Bearer $MIRROR_ADMIN_TOKEN" \
  http://localhost:8844/admin/outbox/status | jq .pending_count'

# 6. Restore Inkwell. Watch pending drain back to 0 (within ~5 minutes
#    given default backoff and current ingest rate).
# 7. Confirm DLQ is still 0 and that no engrams were created without
#    a corresponding pending row (cross-check by sampling engram ids
#    against mirror_pending_receipts.payload->>'context_id').
```

**Pass criteria:**

- pending_count > 0 during the outage (proves enqueue is happening).
- pending_count returns to ~0 within 5 minutes of restore.
- dlq_count remains 0 (no poison rows in a clean run).
- `outbox_status` MCP tool reports `mirror.backend = "native"` and
  `alert_thresholds` exactly matches the contract.

## Inspecting the DLQ

```bash
curl -s -H "Authorization: Bearer $MIRROR_ADMIN_TOKEN" \
  'http://localhost:8844/admin/outbox/dlq?limit=10' | jq
```

Returns up to `limit` (capped at 100) rows ordered newest-first with
`{id, queue_name, payload, attempt_count, last_error, updated_at}`.

## Rollback

The flag is the rollback. Set `MIRROR_OUTBOX_ENABLED=0` and restart
`mirror.service`; callers immediately revert to the legacy
fire-and-forget path. The drain service can be left running — it will
idle on an empty table. The migration is additive (no destructive
changes) and can be left in place.

## Why the drain is its own process

The atomic boundary is "engram + outbox enqueue, single txn" in the
API process. Network egress to Inkwell is unreliable and slow; if the
drain were a thread inside `mirror.service`, a long egress timeout
would tie up an API worker. Splitting them lets the drain crash and
restart without dropping work (rows survive the crash because they're
in Postgres) and lets us scale the two independently if needed.

## Carries

- ADV-H-2/H-3 forensic carries from S023 are unaffected by this work.
- SOS bus outbox is intentionally `best_effort` for now; promoting it
  to a durable substrate is queued for a follow-up F-track in a later
  sprint.
- Inkwell-incoming branch is `not_configured` — Inkwell does not yet
  expose an admin outbox endpoint. Spec lift queued.
