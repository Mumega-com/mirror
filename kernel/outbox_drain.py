"""Mirror outbox drain — POSTs queued receipts to Inkwell.

S024 Track F Phase 2 LOCK F-16.

Long-running worker that pairs with `kernel/outbox.py` (NativeSqlOutbox).
Reads `mirror_pending_receipts` rows via SKIP LOCKED, POSTs them to the
Inkwell receipts endpoint, and confirms / releases / dlqs based on the
result. Runs as the systemd unit
`~/.config/systemd/user/mirror-outbox-drain.service`.

Loop shape:
  1. Claim a pending row (atomic, marks in_flight).
  2. POST payload to Inkwell.
  3. 2xx → confirm (delete row).
     4xx with retryable status → release (back to pending, backoff).
     4xx structural (400/401/403/422) → dlq immediately (won't fix on retry).
     attempts >= max_attempts → dlq.
     network/5xx → release with backoff.
  4. Idle if claim returned None — short sleep before re-poll.

Why a separate process: drain is the part that talks to the outside
world. Pulling it out of request handlers keeps the audit-write
boundary (engram + outbox enqueue, single txn) inside the API process,
and the unreliable network egress in a process that can crash + restart
without dropping work.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Any

from .receipts import InkwellReceiptClient, ReceiptWriterConfig

logger = logging.getLogger("mirror.outbox.drain")

# Status codes where retrying is hopeless — payload shape is wrong.
#
# Adversarial-gate hardening (G_S024_F16_F17_kasra_001):
#  - 401 dropped: token rotation transient. Operator restarts drain;
#    in-flight rows must NOT permanently DLQ during the rotation window.
#  - 404 dropped: Inkwell deploy/route-reshuffle window can transient-404
#    a freshly-cut Worker; permanent-DLQ on 404 = audit-data-loss bug.
#  - 410 dropped: same shape as 404 — distinguishing "permanently gone"
#    from "deploy in flight" is not safe at the drain.
# Operators replay DLQ manually if 401/403 turns out to be permanent.
NON_RETRYABLE_HTTP_STATUS = frozenset({400, 403, 422})

# Visibility timeout — rows stuck in_flight longer than this are reclaimed
# back to pending on every drain startup AND on every Nth claim cycle.
# Closes BLOCK-P0-1 (drain SIGKILL between claim and confirm/release/dlq
# would otherwise pin the row in_flight forever).
STUCK_IN_FLIGHT_TIMEOUT_SEC = int(
    os.getenv("MIRROR_OUTBOX_STUCK_TIMEOUT_SEC", "300")  # 5 min default
)
RECLAIM_EVERY_N_CYCLES = int(os.getenv("MIRROR_OUTBOX_RECLAIM_EVERY", "60"))

IDLE_SLEEP_SEC = float(os.getenv("MIRROR_OUTBOX_IDLE_SLEEP_SEC", "2"))
ERROR_SLEEP_SEC = float(os.getenv("MIRROR_OUTBOX_ERROR_SLEEP_SEC", "5"))


_should_stop = False


def _install_signal_handlers() -> None:
    def _stop(signum, _frame):
        global _should_stop
        logger.info("[outbox-drain] received signal %s; will exit after current cycle", signum)
        _should_stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def _build_client() -> InkwellReceiptClient | None:
    config = ReceiptWriterConfig.from_env()
    if config is None:
        logger.error(
            "[outbox-drain] missing MIRROR_RECEIPT_WRITER_TOKEN / "
            "INKWELL_RECEIPT_TOKEN; drain cannot start"
        )
        return None
    return InkwellReceiptClient(config)


def _post_with_status(client: InkwellReceiptClient, payload: dict[str, Any]) -> tuple[bool, int | None, str]:
    """POST and return (success, http_status, error_text).

    Bypasses InkwellReceiptClient.append (which silent-fails) so we can
    distinguish retryable vs non-retryable failures.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {client.config.token}",
        "X-Substrate-Principal": client.config.principal,
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=client.config.timeout_seconds) as http:
            resp = http.post(client.config.endpoint_url, headers=headers, json=payload)
        if 200 <= resp.status_code < 300:
            return True, resp.status_code, ""
        return False, resp.status_code, f"http {resp.status_code}: {resp.text[:500]}"
    except httpx.HTTPError as exc:
        return False, None, f"network: {type(exc).__name__}: {exc}"
    except Exception as exc:
        return False, None, f"unexpected: {type(exc).__name__}: {exc}"


def run_drain_loop(outbox: Any, client: InkwellReceiptClient) -> int:
    _install_signal_handlers()
    logger.info("[outbox-drain] starting")
    # Startup reclaim — in case a previous drain process was SIGKILL'd
    # between claim() and the terminal call (confirm/release/dlq), pull
    # any row stuck in_flight past the visibility timeout back to
    # pending. Silent-fail-open canon: rows must never be pinned.
    try:
        if hasattr(outbox, "reclaim_stuck_in_flight"):
            n = outbox.reclaim_stuck_in_flight(
                stale_seconds=STUCK_IN_FLIGHT_TIMEOUT_SEC,
            )
            if n:
                logger.warning(
                    "[outbox-drain] startup reclaim: %s stuck in_flight rows → pending",
                    n,
                )
    except Exception as exc:
        logger.error("[outbox-drain] startup reclaim error: %s", exc)

    cycle_count = 0
    while not _should_stop:
        # Periodic sweep — same purpose as startup reclaim, for long-lived
        # drain processes whose own claim cycle managed to stall once.
        cycle_count += 1
        if RECLAIM_EVERY_N_CYCLES > 0 and cycle_count % RECLAIM_EVERY_N_CYCLES == 0:
            try:
                if hasattr(outbox, "reclaim_stuck_in_flight"):
                    n = outbox.reclaim_stuck_in_flight(
                        stale_seconds=STUCK_IN_FLIGHT_TIMEOUT_SEC,
                    )
                    if n:
                        logger.warning(
                            "[outbox-drain] periodic reclaim: %s stuck in_flight rows → pending",
                            n,
                        )
            except Exception as exc:
                logger.error("[outbox-drain] periodic reclaim error: %s", exc)

        try:
            row = outbox.claim()
        except Exception as exc:
            logger.error("[outbox-drain] claim error: %s", exc)
            time.sleep(ERROR_SLEEP_SEC)
            continue

        if row is None:
            time.sleep(IDLE_SLEEP_SEC)
            continue

        ok, status, error_text = _post_with_status(client, row.payload)
        if ok:
            outbox.confirm(row.id)
            logger.info(
                "[outbox-drain] confirmed id=%s status=%s attempt=%s",
                row.id, status, row.attempt_count,
            )
            continue

        # Decide DLQ vs release.
        non_retryable = status in NON_RETRYABLE_HTTP_STATUS
        exhausted = row.attempt_count >= row.max_attempts
        if non_retryable or exhausted:
            outbox.dlq(row.id, error=error_text)
            logger.error(
                "[outbox-drain] dlq id=%s status=%s attempt=%s/%s reason=%s err=%s",
                row.id, status, row.attempt_count, row.max_attempts,
                "non_retryable" if non_retryable else "exhausted",
                error_text,
            )
        else:
            outbox.release(row.id, error=error_text, attempt_count=row.attempt_count)
            logger.warning(
                "[outbox-drain] released id=%s status=%s attempt=%s err=%s",
                row.id, status, row.attempt_count, error_text,
            )

    logger.info("[outbox-drain] exiting")
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.getenv("MIRROR_OUTBOX_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Late imports so this module stays import-safe in tests.
    from .db import LocalDB
    from .outbox import make_outbox

    client = _build_client()
    if client is None:
        return 2

    db = LocalDB()
    outbox = make_outbox(db)
    return run_drain_loop(outbox, client)


if __name__ == "__main__":
    sys.exit(main())
