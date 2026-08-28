"""Dependency resolution: one interface, two implementations, routed by capacity."""

from __future__ import annotations

from pbilineage.resolve.base import (
    DependencyResolver,
    DependencyResult,
    ObjectRef,
    ObjectType,
    ResolvedDependency,
)
from pbilineage.resolve.dax_resolver import DaxDependencyResolver
from pbilineage.resolve.router import CapacityRouter, tier_from_workspace
from pbilineage.resolve.xmla_resolver import XmlaDependencyResolver

__all__ = [
    "CapacityRouter",
    "DaxDependencyResolver",
    "DependencyResolver",
    "DependencyResult",
    "ObjectRef",
    "ObjectType",
    "ResolvedDependency",
    "XmlaDependencyResolver",
    "tier_from_workspace",
]
