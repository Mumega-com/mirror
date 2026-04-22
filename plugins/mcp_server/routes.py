"""
MCP SSE plugin — JSON-RPC 2.0 over HTTP for Claude Desktop + ChatGPT.

Endpoint: POST /mcp/{token}/rpc
SSE stream: GET  /mcp/{token}/sse  (for streaming tool calls)

Token in path (not Authorization header) because Claude Desktop and
ChatGPT pass it that way — they can't always set custom headers.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

from kernel.auth import TokenContext, resolve_token_context
from plugins.mcp_server.tools import TOOLS, call_tool

logger = logging.getLogger("mirror.mcp")

router = APIRouter(prefix="/mcp", tags=["mcp"])

SERVER_INFO = {
    "name": "mirror-mcp",
    "version": "1.0.0",
    "protocolVersion": "2024-11-05",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _auth(token: str) -> TokenContext:
    try:
        return resolve_token_context(f"Bearer {token}")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

def _dispatch(method: str, params: dict, id_: Any, ctx: TokenContext) -> dict:
    if method == "initialize":
        return _ok(id_, {
            "protocolVersion": SERVER_INFO["protocolVersion"],
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}},
        })

    elif method == "tools/list":
        return _ok(id_, {"tools": TOOLS})

    elif method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = call_tool(name, arguments, ctx)
            return _ok(id_, result)
        except ValueError as e:
            return _err(id_, -32601, str(e))
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return _err(id_, -32603, f"Tool error: {e}")

    elif method == "ping":
        return _ok(id_, {})

    else:
        return _err(id_, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/{token}/rpc")
async def mcp_rpc(token: str = Path(...), request: Request = None):
    """JSON-RPC 2.0 endpoint — single request/response."""
    ctx = _auth(token)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _err(None, -32700, "Parse error"),
            status_code=200,
        )

    method = body.get("method", "")
    params = body.get("params") or {}
    id_ = body.get("id")

    response = _dispatch(method, params, id_, ctx)
    return JSONResponse(response)


@router.get("/{token}/sse")
async def mcp_sse(token: str = Path(...)):
    """SSE stream endpoint — for clients that use long-lived SSE connections."""
    ctx = _auth(token)

    async def event_stream():
        # Send server info on connect
        data = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {"serverInfo": SERVER_INFO},
        })
        yield f"data: {data}\n\n"

        # Keep-alive ping every 15 seconds
        while True:
            await asyncio.sleep(15)
            yield "data: {\"jsonrpc\":\"2.0\",\"method\":\"ping\",\"params\":{}}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
