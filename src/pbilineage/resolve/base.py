"""The dependency-resolution interface both paths implement.

The whole point of this module is that the rest of the pipeline never asks
"was this a Premium workspace?". It asks a resolver for a dataset's
dependencies and gets back the same shape either way — with a confidence tag
that says how the answer was obtained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pbilineage.models import Confidence, DatasetSpec


class ObjectType(StrEnum):
    """Object kinds as DISCOVER_CALC_DEPENDENCY names them (normalised)."""

    MEASURE = "measure"
    CALC_COLUMN = "calc_column"
    CALC_TABLE = "calc_table"
    COLUMN = "column"
    TABLE = "table"
    PARTITION = "partition"
    M_EXPRESSION = "m_expression"
    ROWS_ALLOWED = "rows_allowed"  # RLS filter expression
    RELATIONSHIP = "relationship"
    HIERARCHY = "hierarchy"
    UNKNOWN = "unknown"


#: raw DMV OBJECT_TYPE / REFERENCED_OBJECT_TYPE values -> our vocabulary
DMV_OBJECT_TYPES: dict[str, ObjectType] = {
    "MEASURE": ObjectType.MEASURE,
    "CALC_COLUMN": ObjectType.CALC_COLUMN,
    "CALCULATED_COLUMN": ObjectType.CALC_COLUMN,
    "CALC_TABLE": ObjectType.CALC_TABLE,
    "CALCULATED_TABLE": ObjectType.CALC_TABLE,
    "COLUMN": ObjectType.COLUMN,
    "TABLE": ObjectType.TABLE,
    "PARTITION": ObjectType.PARTITION,
    "M_EXPRESSION": ObjectType.M_EXPRESSION,
    "EXPRESSION": ObjectType.M_EXPRESSION,
    "ROWS_ALLOWED": ObjectType.ROWS_ALLOWED,
    "ACTIVE_RELATIONSHIP": ObjectType.RELATIONSHIP,
    "RELATIONSHIP": ObjectType.RELATIONSHIP,
    "HIERARCHY": ObjectType.HIERARCHY,
}


def normalize_object_type(raw: str | None) -> ObjectType:
    if not raw:
        return ObjectType.UNKNOWN
    return DMV_OBJECT_TYPES.get(str(raw).strip().upper(), ObjectType.UNKNOWN)


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A model object: a table plus (for columns/measures) an object name."""

    object_type: ObjectType
    table: str
    name: str = ""

    def display(self) -> str:
        if self.name and self.table:
            return f"'{self.table}'[{self.name}]"
        return self.table or self.name


@dataclass(slots=True)
class ResolvedDependency:
    """`source` is computed from `target` — an edge before it becomes a graph edge."""

    source: ObjectRef
    target: ObjectRef
    confidence: Confidence
    evidence: str
    expression: str = ""
    note: str = ""


@dataclass(slots=True)
class DependencyResult:
    dataset_id: str
    #: which implementation produced this: "xmla-dmv" or "dax-parser"
    path: str
    dependencies: list[ResolvedDependency] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: False when the path was unavailable (no XMLA endpoint, auth failure, ...)
    available: bool = True

    def add(self, dependency: ResolvedDependency) -> None:
        self.dependencies.append(dependency)

    def by_confidence(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for dependency in self.dependencies:
            counts[dependency.confidence.value] = counts.get(dependency.confidence.value, 0) + 1
        return counts


@runtime_checkable
class DependencyResolver(Protocol):
    """Implemented by the XMLA/DMV path and the DAX-parser fallback."""

    #: stable identifier recorded on every edge this resolver produces
    path: str

    def resolve(self, dataset: DatasetSpec) -> DependencyResult:
        """Return every calculated-object dependency in `dataset`."""
        ...
