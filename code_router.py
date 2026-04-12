"""
Mirror Code Router — /code/* endpoints for semantic search over code nodes.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("mirror.code")
router = APIRouter(prefix="/code", tags=["code"])

_db = None
_get_embedding = None


def init(db_client, embed_fn) -> None:
    global _db, _get_embedding
    _db = db_client
    _get_embedding = embed_fn


# --- Models ---

class CodeSearchRequest(BaseModel):
    query: str
    top_k: int = 10
    threshold: float = 0.3
    repo: Optional[str] = None       # filter by repo short name
    kind: Optional[str] = None       # filter by node kind (function/class/etc.)


class CodeNodeResult(BaseModel):
    id: str
    node_id: str
    repo: str
    repo_path: str
    kind: str
    name: str
    qualified_name: Optional[str]
    file_path: str
    line_start: Optional[int]
    line_end: Optional[int]
    language: Optional[str]
    signature: Optional[str]
    similarity: float


# --- Routes ---

@router.post("/search", response_model=list[CodeNodeResult])
async def search_code(request: CodeSearchRequest) -> list[CodeNodeResult]:
    """
    Semantic search over all synced code nodes.

    Example:
        POST /code/search
        {"query": "how does authentication work", "top_k": 5, "repo": "torivers-staging-dev"}
    """
    if not _db or not _get_embedding:
        raise HTTPException(status_code=503, detail="Code search not initialized")

    try:
        embedding = _get_embedding(request.query)
        rows = _db.search_code_nodes(
            embedding=embedding,
            threshold=request.threshold,
            limit=request.top_k,
            repo=request.repo,
            kind=request.kind,
        )

        return [
            CodeNodeResult(
                id=str(row["id"]),
                node_id=row["node_id"],
                repo=row["repo"],
                repo_path=row["repo_path"],
                kind=row["kind"],
                name=row["name"],
                qualified_name=row.get("qualified_name"),
                file_path=row["file_path"],
                line_start=row.get("line_start"),
                line_end=row.get("line_end"),
                language=row.get("language"),
                signature=row.get("signature"),
                similarity=row["similarity"],
            )
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Code search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def code_stats() -> dict:
    """Count of synced code nodes per repo."""
    if not _db:
        raise HTTPException(status_code=503, detail="Not initialized")
    try:
        total, by_repo = _db.code_node_counts()
        return {"total": total, "by_repo": by_repo}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run_sync(repo: Optional[str]) -> None:
    cmd = [sys.executable, "/home/mumega/mirror/mirror_code_sync.py"]
    if repo:
        cmd += ["--repo", repo]
    try:
        subprocess.run(cmd, timeout=3600, check=True)
    except Exception as e:
        logger.error(f"Sync failed: {e}")


@router.post("/sync")
async def trigger_sync(background_tasks: BackgroundTasks, repo: Optional[str] = None) -> dict:
    """
    Trigger a background sync of code nodes into Mirror.
    Optional: ?repo=torivers-staging-dev to sync one repo only.
    """
    background_tasks.add_task(_run_sync, repo)
    return {"status": "sync started", "repo": repo or "all"}
