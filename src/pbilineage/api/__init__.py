"""FastAPI layer in front of the graph store."""

from __future__ import annotations

from pbilineage.api.app import create_app

__all__ = ["create_app"]
