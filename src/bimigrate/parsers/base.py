"""Parser registry and shared parser behavior.

Per-file error isolation is a hard requirement: parse() may record issues
on the inventory but must raise only ParserError, which the bulk engine
catches without killing the batch.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from bimigrate.models.inventory import ArtifactInventory


class ParserError(Exception):
    """Raised for unrecoverable per-file failures (corrupt archive, etc.)."""


class BaseParser(ABC):
    """One parser per source platform."""

    extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, path: Path) -> ArtifactInventory: ...

    @staticmethod
    def fingerprint(inventory: ArtifactInventory) -> str:
        """Stable content hash over the feature shingles (used by dedup)."""
        digest = hashlib.sha256()
        for shingle in sorted(inventory.feature_shingles()):
            digest.update(shingle.encode("utf-8", errors="replace"))
        return digest.hexdigest()[:16]


_REGISTRY: dict[str, type[BaseParser]] = {}


def register(parser_cls: type[BaseParser]) -> type[BaseParser]:
    for ext in parser_cls.extensions:
        _REGISTRY[ext.lower()] = parser_cls
    return parser_cls


def get_parser_for(path: Path) -> BaseParser | None:
    cls = _REGISTRY.get(path.suffix.lower())
    return cls() if cls else None


def registered_extensions() -> list[str]:
    return sorted(_REGISTRY)
