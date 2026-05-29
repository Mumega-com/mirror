"""Console entrypoint for Mirror.

This is a thin wrapper that re-executes ``mirror_api.py`` exactly as if it were
run with ``python mirror_api.py``. It exists purely so packaging can expose a
``mirror-api`` console script without changing any runtime behavior in
``mirror_api.py`` (whose ``if __name__ == "__main__":`` block boots the SOS
service registry, hot-store monitor, bus subscriber, and uvicorn server).

Do not put startup logic here. The single source of truth for startup stays in
``mirror_api.py``.
"""
from __future__ import annotations

import runpy


def main() -> None:
    """Run mirror_api as ``__main__`` — identical to ``python mirror_api.py``."""
    runpy.run_module("mirror_api", run_name="__main__")


if __name__ == "__main__":
    main()
