"""The high-confidence path: ask the engine, don't parse the DAX.

`$SYSTEM.DISCOVER_CALC_DEPENDENCY` returns fully resolved dependencies for
measures, calculated columns, calculated tables, RLS expressions and
partitions — the engine has already done name resolution, context and
ambiguity for us. Every edge from this resolver is tagged `resolved`.

Only workspaces on Premium / PPU / Fabric capacity have an XMLA endpoint, so
the router sends Pro-only workspaces to the DAX-parser fallback instead.
"""

from __future__ import annotations

from typing import Any, Protocol

from pbilineage.models import Confidence, DatasetSpec
from pbilineage.resolve.base import (
    DependencyResult,
    ObjectRef,
    ObjectType,
    ResolvedDependency,
    normalize_object_type,
)

#: the DMV that makes this whole path worth having
CALC_DEPENDENCY_QUERY = "SELECT * FROM $SYSTEM.DISCOVER_CALC_DEPENDENCY"

EVIDENCE = "DISCOVER_CALC_DEPENDENCY"


class XmlaQueryRunner(Protocol):
    """Minimal surface we need from an XMLA connection (see clients.xmla)."""

    def query(self, dataset: DatasetSpec, statement: str) -> list[dict[str, Any]]: ...


def _row(row: dict[str, Any], *names: str) -> str:
    """DMV column names vary in case across client libraries."""
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value)
    return ""


class XmlaDependencyResolver:
    """Resolve dependencies over XMLA. Confidence: `resolved`."""

    path = "xmla-dmv"

    def __init__(self, runner: XmlaQueryRunner) -> None:
        self._runner = runner

    def resolve(self, dataset: DatasetSpec) -> DependencyResult:
        result = DependencyResult(dataset_id=dataset.id, path=self.path)
        try:
            rows = self._runner.query(dataset, CALC_DEPENDENCY_QUERY)
        except Exception as exc:  # connection refused, no endpoint, auth, ...
            result.available = False
            result.warnings.append(f"XMLA dependency query failed for dataset '{dataset.name}': {exc}")
            return result

        for row in rows:
            dependency = self._dependency_from_row(row)
            if dependency is not None:
                result.add(dependency)
        return result

    def _dependency_from_row(self, row: dict[str, Any]) -> ResolvedDependency | None:
        object_type = normalize_object_type(_row(row, "OBJECT_TYPE"))
        referenced_type = normalize_object_type(_row(row, "REFERENCED_OBJECT_TYPE"))

        table = _row(row, "TABLE", "TABLE_NAME")
        name = _row(row, "OBJECT", "OBJECT_NAME")
        ref_table = _row(row, "REFERENCED_TABLE", "REFERENCED_TABLE_NAME")
        ref_name = _row(row, "REFERENCED_OBJECT", "REFERENCED_OBJECT_NAME")

        if not (table or name) or not (ref_table or ref_name):
            return None

        # Relationship rows describe the model's join graph, not column
        # derivation; they are kept but marked so the UI can filter them.
        note = "relationship dependency" if ObjectType.RELATIONSHIP in (object_type, referenced_type) else ""

        return ResolvedDependency(
            source=ObjectRef(object_type, table, name),
            target=ObjectRef(referenced_type, ref_table or table, ref_name),
            confidence=Confidence.RESOLVED,
            evidence=EVIDENCE,
            expression=_row(row, "EXPRESSION"),
            note=note,
        )
