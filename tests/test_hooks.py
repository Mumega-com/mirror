"""Tests for Claude Code hook capture."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def test_capture_exits_zero_without_env(monkeypatch):
    monkeypatch.delenv("MIRROR_API_URL", raising=False)
    monkeypatch.delenv("MIRROR_API_KEY", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mirror.hooks.capture",
            "post_tool_use",
            "Bash",
            '{"command":"npm test"}',
            "all pass",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env={**os.environ, "PYTHONPATH": os.path.dirname(os.path.dirname(__file__))},
        check=False,
    )
    assert result.returncode == 0


def test_post_tool_use_posts_to_mirror_when_configured():
    seen: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(length).decode())
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "PYTHONPATH": os.path.dirname(os.path.dirname(__file__)),
            "MIRROR_API_URL": f"http://127.0.0.1:{server.server_port}",
            "MIRROR_API_KEY": "sk-test",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mirror.hooks.capture",
                "post_tool_use",
                "Bash",
                '{"command":"npm test"}',
                "all pass",
            ],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result.returncode == 0
    assert seen["path"] == "/store"
    assert seen["auth"] == "Bearer sk-test"
    assert "npm test" in seen["body"]["text"]


def test_pre_compact_posts_summary():
    from mirror.hooks import capture

    calls = []
    capture._post_store = lambda content, metadata: calls.append((content, metadata))

    assert capture.main(["pre_compact", "important summary"]) == 0
    assert calls
    assert calls[0][1]["event"] == "pre_compact"
