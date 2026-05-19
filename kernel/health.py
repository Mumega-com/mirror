"""Health check contract — Sprint 006 A.2 (G71).

Uses a fresh psycopg2 connection (not the shared pool) with connect_timeout=1s
and statement_timeout=1000ms to avoid contaminating the pool's session state.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class HealthStatus:
    status: str          # "healthy" | "unhealthy"
    service: str
    db_reachable: bool
    db_reachable_ms: float
    details: Dict[str, Any] = field(default_factory=dict)


async def health_check(db: Any) -> HealthStatus:
    """Ping DB with SELECT 1, 1s timeout. Returns HealthStatus with timing.

    Uses a disposable connection (not the shared pool) so statement_timeout
    and connect_timeout don't leak into normal query paths.
    """
    import psycopg2

    conn_str = getattr(db, "_conn_str", None) or \
        os.getenv("MIRROR_DATABASE_URL") or \
        os.getenv("DATABASE_URL", "")

    t0 = time.monotonic()
    conn = None
    try:
        conn = psycopg2.connect(
            conn_str,
            connect_timeout=1,
            options="-c statement_timeout=1000",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        elapsed_ms = (time.monotonic() - t0) * 1000
        return HealthStatus(
            status="healthy",
            service="mirror",
            db_reachable=True,
            db_reachable_ms=round(elapsed_ms, 1),
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return HealthStatus(
            status="unhealthy",
            service="mirror",
            db_reachable=False,
            db_reachable_ms=round(elapsed_ms, 1),
            details={"reason": str(exc)},
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
