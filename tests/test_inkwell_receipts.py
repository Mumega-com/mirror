from __future__ import annotations

import httpx

from kernel.receipts import (
    InkwellReceiptClient,
    ReceiptWriterConfig,
    build_mirror_engram_write_receipt,
    emit_mirror_engram_write_receipt,
)


def _engram_data() -> dict:
    return {
        "context_id": "task:abc:done",
        "timestamp": "2026-05-02T15:50:00Z",
        "series": "Codex - Agent Memory",
        "project": "mumega",
        "workspace_id": "codex-smoke",
        "owner_type": "agent",
        "owner_id": "agent:codex",
        "epistemic_truths": ["done"],
        "core_concepts": ["integrity"],
        "affective_vibe": "Neutral",
        "energy_level": "Balanced",
        "next_attractor": "",
        "raw_data": {
            "agent": "agent:codex",
            "text": "S037 memory write",
            "metadata": {
                "sos_task_id": "task-123",
                "sos_receipt_id": "receipt-123",
            },
        },
        "tier": "project",
        "entity_id": "codex-smoke",
    }


def test_build_mirror_engram_write_receipt_shape() -> None:
    payload = build_mirror_engram_write_receipt(
        _engram_data(),
        merged=False,
        actor="agent:codex",
    )

    assert payload["tenant_id"] == "codex-smoke"
    assert payload["actor_id"] == "agent:codex"
    assert payload["actor_kind"] == "service"
    assert payload["source_system"] == "mirror"
    assert payload["source_table"] == "engrams"
    assert payload["source_id"] == "task:abc:done"
    assert payload["action_type"] == "mirror.engram.write"
    assert payload["references"]["sos_task_id"] == "task-123"
    assert payload["references"]["sos_receipt_id"] == "receipt-123"
    assert payload["output"] == {"context_id": "task:abc:done", "merged": False}


def test_client_posts_with_mirror_substrate_headers() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["principal"] = request.headers.get("X-Substrate-Principal")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "ok": True,
                "duplicate": False,
                "receipt": {"id": "r2", "chain_seq": 3, "h_self": "abc"},
            },
        )

    client = InkwellReceiptClient(
        ReceiptWriterConfig(
            endpoint_url="https://mumega.com/api/substrate/receipts",
            token="test-token",
        ),
        transport=httpx.MockTransport(handler),
    )

    out = client.append({"source_system": "mirror"})

    assert out and out["ok"] is True
    assert seen["url"] == "https://mumega.com/api/substrate/receipts"
    assert seen["auth"] == "Bearer test-token"
    assert seen["principal"] == "mirror.receipt-writer"
    assert '"source_system":"mirror"' in str(seen["body"])


def test_emit_disabled_without_token(monkeypatch) -> None:
    monkeypatch.delenv("MIRROR_RECEIPT_WRITER_TOKEN", raising=False)
    monkeypatch.delenv("INKWELL_RECEIPT_TOKEN", raising=False)

    assert emit_mirror_engram_write_receipt(_engram_data()) is None
