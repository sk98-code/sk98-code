"""The fallback path: tokenize the DAX ourselves.

Used for Pro-only workspaces, which have no XMLA endpoint. The Scanner API
still hands us the DAX text (`datasetSchema=true&datasetExpressions=true`),
so we tokenize it and resolve the references against the model's own schema.

Everything this resolver emits is tagged `heuristic` — it is a name-matching
exercise, not the engine's answer. Where a bare `[Name]` cannot be resolved
against the schema at all, the dependency is emitted as `opaque` with the
unresolved name recorded, so the gap is visible in the UI rather than silent.
"""

from __future__ import annotations

from pbilineage.models import Confidence, DatasetSpec, TableSpec
from pbilineage.parsers.dax import DaxRefKind, DaxReference, extract_dax_references
from pbilineage.resolve.base import (
    DependencyResult,
    ObjectRef,
    ObjectType,
    ResolvedDependency,
)

EVIDENCE = "dax-tokenizer"


class DaxDependencyResolver:
    """Resolve dependencies by parsing DAX text. Confidence: `heuristic`."""

    path = "dax-parser"

    def resolve(self, dataset: DatasetSpec) -> DependencyResult:
        result = DependencyResult(dataset_id=dataset.id, path=self.path)
        index = _SchemaIndex(dataset)

        for table in dataset.tables:
            for measure in table.measures:
                if measure.expression:
                    self._resolve_expression(
                        result,
                        index,
                        source=ObjectRef(ObjectType.MEASURE, table.name, measure.name),
                        home_table=table,
                        expression=measure.expression,
                    )
            for column in table.columns:
                if column.is_calculated and column.expression:
                    self._resolve_expression(
                        result,
                        index,
                        source=ObjectRef(ObjectType.CALC_COLUMN, table.name, column.name),
                        home_table=table,
                        expression=column.expression,
                    )
            if table.is_calculated:
                expression = next(
                    (p.expression for p in table.partitions if p.source_type == "calculated"), ""
                )
                if expression:
                    self._resolve_expression(
                        result,
                        index,
                        source=ObjectRef(ObjectType.CALC_TABLE, table.name, ""),
                        home_table=table,
                        expression=expression,
                    )
        return result

    def _resolve_expression(
        self,
        result: DependencyResult,
        index: _SchemaIndex,
        source: ObjectRef,
        home_table: TableSpec,
        expression: str,
    ) -> None:
        for reference in extract_dax_references(expression):
            target, confidence, note = index.resolve(reference, home_table)
            if target is None:
                result.warnings.append(f"{source.display()}: unresolved reference {reference.qualified()}")
                continue
            # A measure referring to itself is a tokenizer artefact, not lineage.
            if target.table.lower() == source.table.lower() and target.name.lower() == source.name.lower():
                continue
            result.add(
                ResolvedDependency(
                    source=source,
                    target=target,
                    confidence=confidence,
                    evidence=EVIDENCE,
                    expression=expression,
                    note=note,
                )
            )


class _SchemaIndex:
    """Name lookup over a dataset, used to disambiguate bare `[Name]` refs."""

    def __init__(self, dataset: DatasetSpec) -> None:
        self.dataset = dataset
        self.tables: dict[str, TableSpec] = {t.name.lower(): t for t in dataset.tables}
        self.measures: dict[str, tuple[str, str]] = {}
        #: column name -> [(table, column)] across the whole model
        self.columns: dict[str, list[tuple[str, str]]] = {}
        for table in dataset.tables:
            for measure in table.measures:
                self.measures.setdefault(measure.name.lower(), (table.name, measure.name))
            for column in table.columns:
                self.columns.setdefault(column.name.lower(), []).append((table.name, column.name))

    def resolve(
        self, reference: DaxReference, home_table: TableSpec
    ) -> tuple[ObjectRef | None, Confidence, str]:
        if reference.kind is DaxRefKind.TABLE:
            table = self.tables.get(reference.name.lower())
            if table is None:
                return None, Confidence.OPAQUE, ""
            return ObjectRef(ObjectType.TABLE, table.name), Confidence.HEURISTIC, ""

        if reference.kind is DaxRefKind.COLUMN:
            table = self.tables.get(reference.table.lower())
            if table is None:
                # Referenced table is not in the model we were given.
                return (
                    ObjectRef(ObjectType.COLUMN, reference.table, reference.name),
                    Confidence.OPAQUE,
                    "table not present in the scanned schema",
                )
            column = table.column(reference.name)
            if column is not None:
                kind = ObjectType.CALC_COLUMN if column.is_calculated else ObjectType.COLUMN
                return ObjectRef(kind, table.name, column.name), Confidence.HEURISTIC, ""
            measure = next((m for m in table.measures if m.name.lower() == reference.name.lower()), None)
            if measure is not None:
                # 'Table'[Measure] is legal-but-discouraged syntax that still resolves.
                return ObjectRef(ObjectType.MEASURE, table.name, measure.name), Confidence.HEURISTIC, ""
            return (
                ObjectRef(ObjectType.COLUMN, table.name, reference.name),
                Confidence.OPAQUE,
                "column not present in the scanned schema",
            )

        # Bare [Name]: a measure, or a column of the table the expression lives on.
        lowered = reference.name.lower()
        measure_hit = self.measures.get(lowered)
        if measure_hit is not None:
            return ObjectRef(ObjectType.MEASURE, *measure_hit), Confidence.HEURISTIC, ""

        home_column = home_table.column(reference.name)
        if home_column is not None:
            kind = ObjectType.CALC_COLUMN if home_column.is_calculated else ObjectType.COLUMN
            return ObjectRef(kind, home_table.name, home_column.name), Confidence.HEURISTIC, ""

        candidates = self.columns.get(lowered, [])
        if len(candidates) == 1:
            table_name, column_name = candidates[0]
            return ObjectRef(ObjectType.COLUMN, table_name, column_name), Confidence.HEURISTIC, ""
        if len(candidates) > 1:
            table_name, column_name = candidates[0]
            return (
                ObjectRef(ObjectType.COLUMN, table_name, column_name),
                Confidence.OPAQUE,
                f"[{reference.name}] is ambiguous across {len(candidates)} tables",
            )
        return None, Confidence.OPAQUE, ""
