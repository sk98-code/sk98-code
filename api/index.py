"""Vercel serverless entrypoint for the bimigrate web app.

Vercel scans for a module-level ASGI object; `bimigrate.web.app` only
exposes a `create_app()` factory, so this module instantiates it once per
cold start and publishes it as `app`.

The package lives under `src/`, and Vercel does not `pip install` the
project itself, so `src` is put on `sys.path` before importing.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bimigrate.web.app import create_app  # noqa: E402 — must follow the sys.path setup

app = create_app()
