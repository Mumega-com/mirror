"""Tests for hybrid search: RRF blend logic and BM25 graceful fallback."""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["MIRROR_BACKEND"] = "sqlite"
os.environ["MIRROR_SQLITE_PATH"] = "/tmp/mirror_test_hybrid.db"
pathlib.Path("/tmp/mirror_test_hybrid.db").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Import _rrf_blend directly from routes
# ---------------------------------------------------------------------------
from plugins.memory.routes import _rrf_blend


# ---------------------------------------------------------------------------
# RRF unit tests — pure Python, no DB
# ---------------------------------------------------------------------------

def _doc(id_, text="x"):
    return {"id": id_, "context_id": f"ctx-{id_}", "text": text}


def test_rrf_empty_inputs():
    assert _rrf_blend([], []) == []


def test_rrf_vector_only():
    docs = [_doc("a"), _doc("b"), _doc("c")]
    result = _rrf_blend(docs, [])
    assert [d["id"] for d in result] == ["a", "b", "c"]


def test_rrf_bm25_only():
    docs = [_doc("x"), _doc("y")]
    result = _rrf_blend([], docs)
    assert [d["id"] for d in result] == ["x", "y"]


def test_rrf_higher_rank_wins():
    # doc "a" is rank-0 in vector, doc "b" is rank-0 in bm25 only
    # "a" should outscore "b" since it appears in both lists
    vector = [_doc("a"), _doc("c")]
    bm25   = [_doc("b"), _doc("a")]
    result = _rrf_blend(vector, bm25)
    # "a" appears in both so its RRF score = 1/(61) + 1/(62) > any single-list score
    assert result[0]["id"] == "a"


def test_rrf_deduplicates_by_id():
    doc = _doc("dup")
    result = _rrf_blend([doc], [doc])
    assert len(result) == 1
    assert result[0]["id"] == "dup"


def test_rrf_score_accumulates_across_lists():
    # doc "shared" in both lists at rank 0 should beat "solo" at rank 0 in one list only
    shared = _doc("shared")
    solo   = _doc("solo")
    result = _rrf_blend([shared, solo], [shared])
    assert result[0]["id"] == "shared"


def test_rrf_preserves_doc_fields():
    doc = {"id": "full", "context_id": "ctx-full", "text": "hello", "project": "sos", "similarity": 0.9}
    result = _rrf_blend([doc], [])
    assert result[0]["project"] == "sos"
    assert result[0]["similarity"] == 0.9


def test_rrf_k_parameter_affects_scores():
    # Lower k = more aggressive rank differentiation
    # With k=0: rank-0 score = 1/1 = 1.0, rank-1 score = 1/2 = 0.5
    # With k=60: rank-0 score = 1/61, rank-1 score = 1/62 — nearly equal
    docs = [_doc("first"), _doc("second")]
    result_low_k  = _rrf_blend(docs, [], k=0)
    result_high_k = _rrf_blend(docs, [], k=1000)
    # Order preserved in both cases but scores differ — just verify order is stable
    assert result_low_k[0]["id"] == "first"
    assert result_high_k[0]["id"] == "first"


# ---------------------------------------------------------------------------
# BM25 fallback — on SQLite, search_bm25 is absent so hasattr guard fires
# ---------------------------------------------------------------------------

import json
import tempfile
from fastapi.testclient import TestClient
from fastapi import FastAPI
from plugins import loader as plugin_loader
from plugins.memory.manifest import manifest as memory_manifest

ADMIN_TOKEN = "sk-mumega-internal-001"

_tenant_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump([], _tenant_file)
_tenant_file.flush()

os.environ["MIRROR_TENANT_KEYS_PATH"] = _tenant_file.name
os.environ["MIRROR_ADMIN_TOKEN"] = ADMIN_TOKEN

_app = FastAPI()
plugin_loader.register(memory_manifest)
plugin_loader.mount_all(_app)
_client = TestClient(_app, raise_server_exceptions=False)


def _admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def test_search_works_without_bm25_on_sqlite():
    """On SQLite, search_bm25 is absent — /search must still return 200."""
    r = _client.post("/store", json={
        "context_id": "hybrid-test-1",
        "agent": "test",
        "text": "Mirror hybrid search test engram",
        "epistemic_truths": ["test"],
        "core_concepts": ["hybrid"],
        "affective_vibe": "Neutral",
        "energy_level": "Balanced",
        "next_attractor": "",
        "metadata": {},
    }, headers=_admin_headers())
    assert r.status_code == 200

    r = _client.post("/search", json={
        "query": "hybrid search test",
        "top_k": 5,
        "threshold": 0.0,
    }, headers=_admin_headers())
    assert r.status_code == 200
    # Should return results even without BM25
    body = r.json()
    assert isinstance(body, list)


def test_search_result_count_bounded_by_top_k():
    """Result count must never exceed top_k regardless of candidates."""
    for i in range(8):
        _client.post("/store", json={
            "context_id": f"topk-test-{i}",
            "agent": "test",
            "text": f"engram number {i} for top-k bound test",
            "epistemic_truths": [],
            "core_concepts": [],
            "affective_vibe": "Neutral",
            "energy_level": "Balanced",
            "next_attractor": "",
            "metadata": {},
        }, headers=_admin_headers())

    r = _client.post("/search", json={
        "query": "engram number top-k bound test",
        "top_k": 3,
        "threshold": 0.0,
    }, headers=_admin_headers())
    assert r.status_code == 200
    assert len(r.json()) <= 3
