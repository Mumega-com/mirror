"""Claude Code hook capture command.

All paths are best-effort and intentionally exit 0. Hooks must never block the
editor if Mirror is offline or misconfigured.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen


def _should_capture_post_tool(tool_name: str, tool_input: str, tool_output: str) -> bool:
    if not tool_output.strip():
        return False
    lowered_tool = tool_name.lower()
    if lowered_tool in {"read", "grep", "glob", "ls"}:
        return False
    if lowered_tool == "bash":
        try:
            command = (json.loads(tool_input).get("command") or "").lower()
        except Exception:
            command = tool_input.lower()
        capture_terms = (
            "apply_patch",
            "pytest",
            "test",
            "npm run",
            "pnpm",
            "yarn",
            "git commit",
            "git filter-repo",
        )
        return any(term in command for term in capture_terms)
    return lowered_tool not in {"read", "todowrite"}


def _post_store(content: str, metadata: dict) -> None:
    api_url = os.getenv("MIRROR_API_URL")
    api_key = os.getenv("MIRROR_API_KEY")
    if not api_url or not api_key:
        return

    body = {
        "agent": os.getenv("MIRROR_AGENT", "claude-code"),
        "context_id": f"claude-hook-{datetime.now(timezone.utc).isoformat()}",
        "text": content,
        "metadata": metadata,
        "core_concepts": ["claude-code", "hook"],
    }
    req = Request(
        f"{api_url.rstrip('/')}/store",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=3):
            pass
    except (OSError, URLError, Exception):
        return


def post_tool_use(tool_name: str, tool_input: str, tool_output: str) -> None:
    if not _should_capture_post_tool(tool_name, tool_input, tool_output):
        return
    content = f"Claude Code tool result: {tool_name}\n\nInput:\n{tool_input}\n\nOutput:\n{tool_output[:4000]}"
    _post_store(content, {"event": "post_tool_use", "tool_name": tool_name})


def session_end(session_id: str) -> None:
    content = f"Claude Code session ended: {session_id}"
    _post_store(content, {"event": "session_end", "session_id": session_id})


def pre_compact(context_summary: str) -> None:
    if not context_summary.strip():
        return
    _post_store(
        f"Claude Code pre-compact summary:\n\n{context_summary[:8000]}",
        {"event": "pre_compact", "tier": "episodic"},
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        command = argv[0] if argv else ""
        if command == "post_tool_use":
            post_tool_use(argv[1] if len(argv) > 1 else "", argv[2] if len(argv) > 2 else "", argv[3] if len(argv) > 3 else "")
        elif command == "session_end":
            session_end(argv[1] if len(argv) > 1 else "")
        elif command == "pre_compact":
            pre_compact(argv[1] if len(argv) > 1 else "")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
