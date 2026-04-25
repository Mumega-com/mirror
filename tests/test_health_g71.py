"""
§G71 Mirror health check tests — Sprint 006 A.2.

Unit tests: /health endpoint response shape, status codes, transition emit.
Integration tests (requires DB): live health_check() against real PG.

Run all:   MIRROR_DATABASE_URL=... pytest tests/test_health_g71.py -v
Run unit:  pytest tests/test_health_g71.py -v -m "not db"
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure mirror package root and SOS root are on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, "/home/mumega/SOS")

# Load env before _has_db() is evaluated for skip markers
try:
    from dotenv import load_dotenv as _ldenv
    _ldenv("/home/mumega/.env.secrets")
    _ldenv("/home/mumega/mirror/.env", override=True)
except ImportError:
    pass

from kernel.health import HealthStatus, health_check


# ── helpers ───────────────────────────────────────────────────────────────────

def _has_db() -> bool:
    return bool(os.getenv("MIRROR_DATABASE_URL") or os.getenv("DATABASE_URL"))


db_required = pytest.mark.skipif(not _has_db(), reason="Mirror DB not configured")


def _mock_db(conn_str: str = "postgresql://localhost/test") -> MagicMock:
    m = MagicMock()
    m._conn_str = conn_str
    return m


# ── TC-G71a: healthy response shape ──────────────────────────────────────────


class TestHealthShape:
    def test_healthy_status_fields(self) -> None:
        """TC-G71a (unit): HealthStatus has all required fields with correct types."""
        s = HealthStatus(
            status="healthy",
            service="mirror",
            db_reachable=True,
            db_reachable_ms=4.2,
        )
        assert s.status == "healthy"
        assert s.db_reachable is True
        assert isinstance(s.db_reachable_ms, float)
        assert s.service == "mirror"

    def test_unhealthy_status_fields(self) -> None:
        """TC-G71b (unit): HealthStatus unhealthy has db_reachable=False."""
        s = HealthStatus(
            status="unhealthy",
            service="mirror",
            db_reachable=False,
            db_reachable_ms=1001.0,
            details={"reason": "connection refused"},
        )
        assert s.status == "unhealthy"
        assert s.db_reachable is False
        assert "reason" in s.details

    def test_details_defaults_empty(self) -> None:
        s = HealthStatus(status="healthy", service="mirror", db_reachable=True, db_reachable_ms=5.0)
        assert s.details == {}


# ── TC-G71b: health_check() returns healthy on good DB ───────────────────────


class TestHealthCheckUnit:
    @pytest.mark.asyncio
    async def test_healthy_on_successful_ping(self) -> None:
        """TC-G71b (unit): health_check returns healthy when SELECT 1 succeeds."""
        db = _mock_db("postgresql://localhost/test")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("psycopg2.connect", return_value=mock_conn):
            result = await health_check(db)

        assert result.status == "healthy"
        assert result.db_reachable is True
        assert result.db_reachable_ms >= 0.0

    @pytest.mark.asyncio
    async def test_unhealthy_on_connection_error(self) -> None:
        """TC-G71b (unit): health_check returns unhealthy when psycopg2.connect raises."""
        import psycopg2
        db = _mock_db("postgresql://bad-host/test")

        with patch("psycopg2.connect", side_effect=psycopg2.OperationalError("connection refused")):
            result = await health_check(db)

        assert result.status == "unhealthy"
        assert result.db_reachable is False
        assert result.db_reachable_ms >= 0.0
        assert "reason" in result.details

    @pytest.mark.asyncio
    async def test_unhealthy_on_query_timeout(self) -> None:
        """TC-G71b (unit): health_check returns unhealthy when query raises."""
        import psycopg2
        db = _mock_db("postgresql://localhost/test")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute.side_effect = psycopg2.OperationalError("statement timeout")
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("psycopg2.connect", return_value=mock_conn):
            result = await health_check(db)

        assert result.status == "unhealthy"
        assert result.db_reachable is False

    @pytest.mark.asyncio
    async def test_conn_str_from_db_attr(self) -> None:
        """TC-G71c (unit): health_check reads _conn_str from db object."""
        db = _mock_db("postgresql://myhost/mydb")
        captured = {}

        def _fake_connect(dsn, **kwargs):
            captured["dsn"] = dsn
            raise RuntimeError("stop here")

        with patch("psycopg2.connect", side_effect=_fake_connect):
            await health_check(db)

        assert captured.get("dsn") == "postgresql://myhost/mydb"


# ── TC-G71d: transition-only emit ─────────────────────────────────────────────


class TestTransitionEmit:
    def test_emit_fires_on_transition(self) -> None:
        """TC-G71d: emit_mirror_health is called when status changes."""
        from sos.observability.sprint_telemetry import emit_mirror_health

        with patch("sos.observability.sprint_telemetry.json") as mock_json, \
             patch("sos.observability.sprint_telemetry.SOS_REPO") as mock_repo:
            # Patch audit_emit to avoid DB dependency
            import sos.observability.sprint_telemetry as _st
            orig = getattr(_st, "_audit_emit_available", None)
            with patch.dict("sys.modules", {"sos.kernel.audit_chain": None}):
                result = emit_mirror_health(
                    instance_id="18844",
                    prev_status="healthy",
                    new_status="unhealthy",
                    db_reachable_ms=1001.0,
                )

        assert result["instance_id"] == "18844"
        assert result["prev_status"] == "healthy"
        assert result["new_status"] == "unhealthy"
        assert result["db_reachable_ms"] == 1001.0

    def test_emit_payload_shape(self) -> None:
        """TC-G71d: emit_mirror_health returns complete payload."""
        from sos.observability.sprint_telemetry import emit_mirror_health

        with patch.dict("sys.modules", {"sos.kernel.audit_chain": None}), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.write_text"):
            result = emit_mirror_health(
                instance_id="18845",
                prev_status="unhealthy",
                new_status="healthy",
                db_reachable_ms=3.7,
            )

        expected_keys = {"instance_id", "prev_status", "new_status", "db_reachable_ms", "emitted_by", "ts"}
        assert set(result.keys()) == expected_keys
        assert result["emitted_by"] == "mirror"


# ── TC-G71e: live DB integration tests ───────────────────────────────────────


@db_required
class TestHealthCheckLiveDB:
    @pytest.mark.asyncio
    async def test_live_ping_returns_healthy(self) -> None:
        """TC-G71e: live health_check against real DB returns healthy."""
        from kernel.db import get_db
        live_db = get_db()
        result = await health_check(live_db)
        assert result.status == "healthy"
        assert result.db_reachable is True
        assert result.db_reachable_ms > 0.0
        assert result.db_reachable_ms < 2000.0  # should be well under 1s

    @pytest.mark.asyncio
    async def test_live_ping_ms_is_reasonable(self) -> None:
        """TC-G71e: live DB ping completes in <500ms (not timeout-bound)."""
        from kernel.db import get_db
        live_db = get_db()
        result = await health_check(live_db)
        # 500ms threshold — local PG should be much faster
        assert result.db_reachable_ms < 500.0, (
            f"DB ping took {result.db_reachable_ms}ms — expected <500ms"
        )
