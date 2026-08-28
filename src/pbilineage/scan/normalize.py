"""Normalise raw Scanner API results into a `TenantSnapshot`.

The scan result is a large, loosely-typed document whose shape varies with
the artifact type and the tenant's feature set. Everything defensive about
reading it lives here, so the rest of the pipeline can work with typed
objects and assume nothing about API versions.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from pbilineage.models import (
    CapacityTier,
    ColumnSpec,
    DataflowSpec,
    DataSourceSpec,
    DatasetSpec,
    MeasureSpec,
    PartitionSpec,
    ReportSpec,
    TableSpec,
    TenantSnapshot,
    WorkspaceSpec,
)
from pbilineage.resolve.router import tier_from_workspace

__all__ = [
    "classify_partition_expression",
    "dataflow_queries_from_model_json",
    "snapshot_from_scan_results",
]

#: Scanner API columnType values that mean "not a plain imported column"
CALCULATED_COLUMN_TYPES = {"calculated", "calculatedtablecolumn"}

_M_PROLOGUE = re.compile(r"^\s*(//[^\n]*\n|/\*.*?\*/|\s)*let\b", re.IGNORECASE | re.DOTALL)


def classify_partition_expression(expression: str) -> str:
    """Is this partition source M or a DAX calculated table?

    The Scanner API hands back the expression text without saying which
    language it is. `let ... in` is decisive for M; a DAX calculated table
    starts with a table expression instead.
    """
    text = (expression or "").strip()
    if not text:
        return "unknown"
    if _M_PROLOGUE.match(text):
        return "m"
    if re.match(r'^\s*#?"?[A-Za-z_]', text) and "=" in text and "let" not in text.lower():
        # e.g. Sql.Database("srv","db") one-liners are still M
        if re.match(r"^\s*[A-Za-z][A-Za-z0-9_]*\.[A-Za-z]", text):
            return "m"
    if re.match(r"^\s*[A-Za-z][A-Za-z0-9_]*\.[A-Za-z]", text):
        return "m"
    return "calculated"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any, default: str = "") -> str:
    return str(value) if value not in (None, "") else default


def _data_source_from_instance(entry: dict[str, Any]) -> DataSourceSpec:
    details = entry.get("connectionDetails") or {}
    if not isinstance(details, dict):
        details = {}
    return DataSourceSpec(
        id=_str(entry.get("datasourceId")),
        kind=_str(entry.get("datasourceType"), "Unknown"),
        server=_str(details.get("server") or details.get("url") or details.get("account")),
        database=_str(details.get("database") or details.get("workspaceId")),
        path=_str(details.get("path") or details.get("domain") or details.get("class")),
    )


def _table_from_scan(entry: dict[str, Any]) -> TableSpec:
    table = TableSpec(name=_str(entry.get("name")), description=_str(entry.get("description")))

    for column in _as_list(entry.get("columns")):
        if not isinstance(column, dict):
            continue
        column_type = _str(column.get("columnType")).lower()
        table.columns.append(
            ColumnSpec(
                name=_str(column.get("name")),
                data_type=_str(column.get("dataType")),
                is_calculated=column_type in CALCULATED_COLUMN_TYPES,
                expression=_str(column.get("expression")),
                is_hidden=bool(column.get("isHidden", False)),
                description=_str(column.get("description")),
            )
        )

    for measure in _as_list(entry.get("measures")):
        if not isinstance(measure, dict):
            continue
        table.measures.append(
            MeasureSpec(
                name=_str(measure.get("name")),
                table=table.name,
                expression=_str(measure.get("expression")),
                is_hidden=bool(measure.get("isHidden", False)),
                description=_str(measure.get("description")),
            )
        )

    for index, partition in enumerate(_as_list(entry.get("source"))):
        expression = ""
        if isinstance(partition, dict):
            expression = _str(partition.get("expression"))
        elif isinstance(partition, str):
            expression = partition
        source_type = classify_partition_expression(expression)
        if source_type == "calculated":
            table.is_calculated = True
        table.partitions.append(
            PartitionSpec(
                name=f"{table.name}-{index + 1}" if index else table.name,
                source_type=source_type,
                expression=expression,
            )
        )

    # A table whose every column is a calculated-table column is a calc table
    # even when the partition text did not make that obvious.
    if table.columns and all(c.is_calculated for c in table.columns) and table.partitions:
        if any(p.source_type == "calculated" for p in table.partitions):
            table.is_calculated = True

    return table


def _dataset_from_scan(
    entry: dict[str, Any], workspace_id: str, instances: dict[str, DataSourceSpec]
) -> DatasetSpec:
    dataset = DatasetSpec(
        id=_str(entry.get("id")),
        name=_str(entry.get("name")),
        workspace_id=workspace_id,
        configured_by=_str(entry.get("configuredBy")),
    )
    for table in _as_list(entry.get("tables")):
        if isinstance(table, dict):
            dataset.tables.append(_table_from_scan(table))

    for expression in _as_list(entry.get("expressions")):
        if isinstance(expression, dict) and expression.get("name"):
            dataset.expressions[_str(expression["name"])] = _str(expression.get("expression"))

    dataset.data_sources = _usages_to_sources(entry.get("datasourceUsages"), instances)
    return dataset


def _usages_to_sources(usages: Any, instances: dict[str, DataSourceSpec]) -> list[DataSourceSpec]:
    sources: list[DataSourceSpec] = []
    for usage in _as_list(usages):
        if not isinstance(usage, dict):
            continue
        instance_id = _str(usage.get("datasourceInstanceId"))
        source = instances.get(instance_id)
        if source is not None:
            sources.append(source)
        elif instance_id:
            sources.append(DataSourceSpec(id=instance_id, kind="Unknown"))
    return sources


def dataflow_queries_from_model_json(model_json: dict[str, Any]) -> dict[str, str]:
    """Extract entity -> M script from a dataflow's exported `model.json`.

    Dataflow definitions keep the whole mashup in one document
    (`pbi:mashup.document`) with the entities listed separately, so the
    per-entity query is recovered by splitting the shared `let` block on its
    `shared <Name> =` declarations where they exist.
    """
    mashup = model_json.get("pbi:mashup") or {}
    document = ""
    if isinstance(mashup, dict):
        document = _str(mashup.get("document"))
    if not document:
        document = _str(model_json.get("document"))

    entity_names = [
        _str(entity.get("name"))
        for entity in _as_list(model_json.get("entities"))
        if isinstance(entity, dict) and entity.get("name")
    ]
    if not document:
        return {name: "" for name in entity_names}

    queries: dict[str, str] = {}
    # `section Section1; shared Query = let ... in ...;`
    for match in re.finditer(
        r'shared\s+(#?"[^"]+"|[A-Za-z_][\w.]*)\s*=\s*(.*?);(?=\s*(?:shared\b|$))',
        document,
        re.DOTALL,
    ):
        name = match.group(1).strip().strip('#"')
        queries[name] = match.group(2).strip()

    for name in entity_names:
        queries.setdefault(name, document)
    return queries or {"Query": document}


def snapshot_from_scan_results(
    scan_results: Sequence[dict[str, Any]] | Iterable[dict[str, Any]],
    capacity_skus: dict[str, str] | None = None,
) -> TenantSnapshot:
    """Merge one or more `scanResult` payloads into a typed snapshot."""
    snapshot = TenantSnapshot()
    skus = capacity_skus or {}

    for result in scan_results:
        if not isinstance(result, dict):
            continue
        instances = {
            _str(entry.get("datasourceId")): _data_source_from_instance(entry)
            for entry in _as_list(result.get("datasourceInstances"))
            if isinstance(entry, dict)
        }

        for raw in _as_list(result.get("workspaces")):
            if not isinstance(raw, dict):
                continue
            workspace_id = _str(raw.get("id"))
            if not workspace_id:
                continue
            workspace = WorkspaceSpec(
                id=workspace_id,
                name=_str(raw.get("name"), workspace_id),
                capacity_id=_str(raw.get("capacityId")),
                capacity_sku=_str(raw.get("capacitySku") or skus.get(_str(raw.get("capacityId")))),
                is_personal=_str(raw.get("type")).lower() == "personalgroup",
                state=_str(raw.get("state")),
            )
            if not workspace.capacity_id and raw.get("isOnDedicatedCapacity"):
                workspace.capacity_id = "unknown-capacity"
            workspace.tier = tier_from_workspace(workspace, skus)

            for dataset in _as_list(raw.get("datasets")):
                if isinstance(dataset, dict) and dataset.get("id"):
                    workspace.datasets.append(_dataset_from_scan(dataset, workspace_id, instances))

            for report in _as_list(raw.get("reports")):
                if not isinstance(report, dict) or not report.get("id"):
                    continue
                workspace.reports.append(
                    ReportSpec(
                        id=_str(report.get("id")),
                        name=_str(report.get("name"), "Untitled report"),
                        workspace_id=workspace_id,
                        dataset_id=_str(report.get("datasetId")),
                    )
                )

            for dataflow in _as_list(raw.get("dataflows")):
                if not isinstance(dataflow, dict):
                    continue
                dataflow_id = _str(dataflow.get("objectId") or dataflow.get("id"))
                if not dataflow_id:
                    continue
                workspace.dataflows.append(
                    DataflowSpec(
                        id=dataflow_id,
                        name=_str(dataflow.get("name"), "Untitled dataflow"),
                        workspace_id=workspace_id,
                        data_sources=_usages_to_sources(dataflow.get("datasourceUsages"), instances),
                    )
                )

            if workspace.tier is CapacityTier.PRO:
                snapshot.warnings.append(
                    f"workspace '{workspace.name}' is on shared capacity: no XMLA endpoint, "
                    "so its DAX dependencies are parsed rather than resolved"
                )
            snapshot.workspaces.append(workspace)

    return snapshot
