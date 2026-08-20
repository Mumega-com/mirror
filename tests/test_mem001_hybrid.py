"""MEM-001 descendant tests: auth fail-closed, search bounds, hybrid RRF."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from kernel.auth import resolve_token_context
from kernel.types import SearchRequest
from plugins.memory.routes import _rrf_blend, search_memory
from kernel.auth import TokenContext


HARDCODED_FALLBACK = "sk-mumega-internal-001"


def test_hardcoded_admin_fallback_is_gone():
    import inspect
    import kernel.auth as auth

    source = inspect.getsource(auth)
    assert "_FALLBACK_ADMIN_TOKEN" not in source
    assert HARDCODED_FALLBACK not in source


def test_unset_admin_env_does_not_accept_legacy_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("MIRROR_ADMIN_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        resolve_token_context(
            f"Bearer {HARDCODED_FALLBACK}",
            tenant_keys_path=str(tmp_path / "empty.json"),
        )
    assert exc.value.status_code == 401


def test_empty_admin_env_does_not_match_empty_bearer(monkeypatch, tmp_path):
    monkeypatch.setenv("MIRROR_ADMIN_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        resolve_token_context(
            "Bearer ",
            tenant_keys_path=str(tmp_path / "empty.json"),
        )
    assert exc.value.status_code == 401


def test_explicit_admin_token_still_works(tmp_path):
    ctx = resolve_token_context(
        "Bearer secret-admin",
        admin_token="secret-admin",
        tenant_keys_path=str(tmp_path / "empty.json"),
    )
    assert ctx.is_admin is True


@pytest.mark.parametrize("query", ["", "   "])
def test_search_request_rejects_empty_query(query):
    with pytest.raises(ValidationError):
        SearchRequest(query=query)


def test_search_request_rejects_out_of_range_top_k():
    with pytest.raises(ValidationError):
        SearchRequest(query="hello", top_k=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="hello", top_k=51)
    ok = SearchRequest(query=" hello ", top_k=5)
    assert ok.query == "hello"
    assert ok.top_k == 5


def test_rrf_blend_ranks_docs_in_both_lists_higher():
    vector = [{"id": "a"}, {"id": "b"}]
    bm25 = [{"id": "b"}, {"id": "c"}]
    blended = _rrf_blend(vector, bm25)
    ids = [d["id"] for d in blended]
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_search_route_rejects_unbounded_top_k(monkeypatch):
    req = SearchRequest(query="ok", top_k=5)
    req.top_k = 99  # bypass model; route must still fail closed
    ctx = TokenContext(workspace_id="ws", owner_type="agent", owner_id="a")
    with pytest.raises(HTTPException) as exc:
        await search_memory(req, ctx)
    assert exc.value.status_code == 422
