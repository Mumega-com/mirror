from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ReceiptWriterConfig:
    endpoint_url: str
    token: str
    principal: str = "mirror.receipt-writer"
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "ReceiptWriterConfig | None":
        token = os.getenv("MIRROR_RECEIPT_WRITER_TOKEN") or os.getenv("INKWELL_RECEIPT_TOKEN")
        if not token:
            return None

        endpoint = os.getenv("INKWELL_RECEIPTS_URL")
        if not endpoint:
            base_url = (
                os.getenv("INKWELL_API_URL")
                or os.getenv("INKWELL_URL")
                or "https://mumega.com"
            ).rstrip("/")
            endpoint = f"{base_url}/api/substrate/receipts"

        try:
            timeout_seconds = max(0.5, float(os.getenv("INKWELL_RECEIPT_TIMEOUT_SECONDS", "5")))
        except ValueError:
            timeout_seconds = 5.0

        return cls(
            endpoint_url=endpoint,
            token=token,
            principal=os.getenv("MIRROR_RECEIPT_PRINCIPAL", "mirror.receipt-writer"),
            timeout_seconds=timeout_seconds,
        )


class InkwellReceiptClient:
    def __init__(
        self,
        config: ReceiptWriterConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    def append(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "X-Substrate-Principal": self.config.principal,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.config.timeout_seconds, transport=self._transport) as client:
                response = client.post(self.config.endpoint_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else None
        except Exception:
            return None


def _metadata(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("raw_data")
    if isinstance(raw, dict) and isinstance(raw.get("metadata"), dict):
        return raw["metadata"]
    return {}


def build_mirror_engram_write_receipt(
    data: dict[str, Any],
    *,
    merged: bool = False,
    actor: str | None = None,
) -> dict[str, Any]:
    metadata = _metadata(data)
    sos_task_id = (
        metadata.get("sos_task_id")
        or metadata.get("task_id")
        or metadata.get("source_task_id")
    )
    sos_receipt_id = metadata.get("sos_receipt_id")
    context_id = str(data["context_id"])

    references: dict[str, Any] = {
        "agent": (data.get("raw_data") or {}).get("agent") if isinstance(data.get("raw_data"), dict) else None,
        "workspace_id": data.get("workspace_id"),
        "owner_type": data.get("owner_type"),
        "owner_id": data.get("owner_id"),
        "project": data.get("project"),
        "merged": merged,
    }
    if sos_task_id:
        references["sos_task_id"] = sos_task_id
    if sos_receipt_id:
        references["sos_receipt_id"] = sos_receipt_id

    return {
        "tenant_id": data.get("workspace_id") or data.get("project") or "global",
        "actor_id": actor or "mirror.receipt-writer",
        "actor_kind": "service",
        "source_system": "mirror",
        "source_table": "engrams",
        "source_id": context_id,
        "action_type": "mirror.engram.write",
        "input": {
            "context_id": context_id,
            "series": data.get("series"),
            "project": data.get("project"),
            "tier": data.get("tier", "project"),
            "entity_id": data.get("entity_id"),
            "core_concepts": data.get("core_concepts", []),
            "epistemic_truths": data.get("epistemic_truths", []),
        },
        "output": {
            "context_id": context_id,
            "merged": merged,
        },
        "references": references,
    }


def emit_mirror_engram_write_receipt(
    data: dict[str, Any],
    *,
    merged: bool = False,
    actor: str | None = None,
    client: InkwellReceiptClient | None = None,
) -> dict[str, Any] | None:
    if client is None:
        config = ReceiptWriterConfig.from_env()
        if config is None:
            return None
        client = InkwellReceiptClient(config)

    return client.append(
        build_mirror_engram_write_receipt(data, merged=merged, actor=actor),
    )
