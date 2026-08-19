"""TMSCHEMA / DISCOVER_CALC_DEPENDENCY ingest (build spec §4.3–4.4).

Everything runs against a `DmvExecutor` — any object with
`query(dmv_query) -> list[dict]`. The live implementation is
`connectors.local_as.PyadomdExecutor`; tests inject canned rowsets. The
normalization here is the only place that knows TMSCHEMA's numeric enums
and ID-joins; the analysis core sees the same `Model` the file readers
produce (spec Section 2's connector-layer rule).

Numeric enum values follow MS-SSAS-T / TOM. Unknown values pass through as
`"<name><value>"` rather than failing — engines add enum members faster
than tools track them.
"""

from __future__ import annotations

from typing import Any, Protocol

from pbi_lineage.schema import (
    CalcDependency,
    CalculationGroup,
    CalculationItem,
    Column,
    Hierarchy,
    HierarchyLevel,
    Measure,
    Model,
    NamedExpression,
    Partition,
    Relationship,
    Role,
    Table,
)


class DmvExecutor(Protocol):
    def query(self, dmv_query: str) -> list[dict]: ...


_DATA_TYPES = {
    1: "automatic",
    2: "string",
    6: "int64",
    8: "double",
    9: "dateTime",
    10: "decimal",
    11: "boolean",
    17: "binary",
    19: "unknown",
    20: "variant",
}
_SUMMARIZE_BY = {
    1: "default",
    2: "none",
    3: "sum",
    4: "min",
    5: "max",
    6: "count",
    7: "average",
    8: "distinctCount",
}
_PARTITION_TYPES = {
    1: "query",
    2: "calculated",
    3: "none",
    4: "m",
    5: "entity",
    6: "policyRange",
    7: "calculationGroup",
    8: "inferred",
}
_PARTITION_MODES = {0: "default", 1: "import", 2: "directQuery", 3: "push", 4: "dual"}
_CARDINALITY = {0: "none", 1: "one", 2: "many"}
_CROSS_FILTERING = {1: "oneDirection", 2: "bothDirections", 3: "automatic"}
_MODEL_PERMISSIONS = {1: "none", 2: "read", 3: "readRefresh", 4: "refresh", 5: "administrator"}

_ROW_NUMBER_COLUMN_TYPE = 3  # internal RowNumber columns are not inventory

# VertiPaq's internal structures are published alongside real tables by some
# extractors: H$ attribute hierarchies, U$ user hierarchies, R$ relationship
# indexes. They are storage artifacts, not model objects, and must never
# appear in the inventory (their bytes are attributed in `size.py` instead).
_INTERNAL_TABLE_PREFIXES = ("H$", "U$", "R$")


def _is_internal_table(name: Any) -> bool:
    return isinstance(name, str) and name.startswith(_INTERNAL_TABLE_PREFIXES)


def _get(row: dict, *names: str, default: Any = None) -> Any:
    """Case-insensitive column lookup — ADOMD clients differ on casing."""
    for name in names:
        if name in row:
            return row[name]
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return default


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _enum(mapping: dict[int, str], value: Any, label: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return str(value)
    return mapping.get(value, f"{label}{value}")


def _query(executor: DmvExecutor, dmv: str, warnings: list[str]) -> list[dict]:
    """One DMV, tolerantly: an engine that lacks it yields [] + a warning,
    not a failed ingest."""
    try:
        return executor.query(f"SELECT * FROM $SYSTEM.{dmv}") or []
    except Exception as exc:  # noqa: BLE001 — provider exception types vary
        warnings.append(f"{dmv}: {exc}")
        return []


def read_model_via_dmv(executor: DmvExecutor, *, name: str = "", warnings: list[str] | None = None) -> Model:
    """Full TMSCHEMA inventory → normalized Model (capability C1)."""
    warnings = warnings if warnings is not None else []
    model = Model(name=name)

    table_rows = _query(executor, "TMSCHEMA_TABLES", warnings)
    column_rows = _query(executor, "TMSCHEMA_COLUMNS", warnings)
    measure_rows = _query(executor, "TMSCHEMA_MEASURES", warnings)
    hierarchy_rows = _query(executor, "TMSCHEMA_HIERARCHIES", warnings)
    level_rows = _query(executor, "TMSCHEMA_LEVELS", warnings)
    partition_rows = _query(executor, "TMSCHEMA_PARTITIONS", warnings)
    relationship_rows = _query(executor, "TMSCHEMA_RELATIONSHIPS", warnings)
    role_rows = _query(executor, "TMSCHEMA_ROLES", warnings)
    permission_rows = _query(executor, "TMSCHEMA_TABLE_PERMISSIONS", warnings)
    expression_rows = _query(executor, "TMSCHEMA_EXPRESSIONS", warnings)
    calc_group_rows = _query(executor, "TMSCHEMA_CALCULATION_GROUPS", warnings)
    calc_item_rows = _query(executor, "TMSCHEMA_CALCULATION_ITEMS", warnings)
    format_string_rows = _query(executor, "TMSCHEMA_FORMAT_STRING_DEFINITIONS", warnings)

    format_strings = {_get(row, "ID"): _get(row, "Expression", default="") for row in format_string_rows}

    # ---- columns keyed by ID first: sort-by / level / relationship joins --
    columns_by_id: dict[Any, Column] = {}
    column_table: dict[Any, Any] = {}
    column_table_name: dict[Any, str] = {}
    for row in column_rows:
        if _get(row, "Type") is not None and int(_get(row, "Type")) == _ROW_NUMBER_COLUMN_TYPE:
            continue
        if _is_internal_table(_get(row, "TableName")):
            continue
        column = Column(
            name=_get(row, "ExplicitName", "InferredName", "Name") or "",
            data_type=_enum(_DATA_TYPES, _get(row, "ExplicitDataType", "DataType"), "dataType"),
            is_hidden=_bool(_get(row, "IsHidden")),
            is_key=_bool(_get(row, "IsKey")),
            is_calculated=(_get(row, "Type") is not None and int(_get(row, "Type")) in (2, 4)),
            expression=_get(row, "Expression") or None,
            source_column=_get(row, "SourceColumn") or None,
            summarize_by=_enum(_SUMMARIZE_BY, _get(row, "SummarizeBy"), "summarizeBy"),
            format_string=_get(row, "FormatString") or None,
            data_category=_get(row, "DataCategory") or None,
        )
        columns_by_id[_get(row, "ID")] = column
        column_table[_get(row, "ID")] = _get(row, "TableID")
        if _get(row, "TableName"):
            column_table_name[_get(row, "ID")] = str(_get(row, "TableName"))
    for row in column_rows:
        sort_by = _get(row, "SortByColumnID")
        if sort_by and sort_by in columns_by_id and _get(row, "ID") in columns_by_id:
            columns_by_id[_get(row, "ID")].sort_by_column = columns_by_id[sort_by].name

    # ---- tables ----------------------------------------------------------
    tables_by_id: dict[Any, Table] = {}
    for row in table_rows:
        table = Table(
            name=_get(row, "Name", default=""),
            is_hidden=_bool(_get(row, "IsHidden")),
            data_category=_get(row, "DataCategory") or None,
        )
        tables_by_id[_get(row, "ID")] = table
        model.tables.append(table)
    for column_id, column in columns_by_id.items():
        table = tables_by_id.get(column_table[column_id])
        if table is None:
            # Some extractors publish fewer tables than columns (auto
            # date/time tables in particular). Dropping the columns would
            # hide real objects, so rebuild the parent from its name.
            fallback_name = column_table_name.get(column_id)
            if fallback_name and not _is_internal_table(fallback_name):
                table = next((x for x in model.tables if x.name == fallback_name), None)
                if table is None:
                    table = Table(name=fallback_name)
                    model.tables.append(table)
                tables_by_id.setdefault(column_table[column_id], table)
        if table is not None:
            table.columns.append(column)

    for row in measure_rows:
        table = tables_by_id.get(_get(row, "TableID"))
        if table is None:
            continue
        table.measures.append(
            Measure(
                name=_get(row, "Name", default=""),
                expression=_get(row, "Expression") or "",
                format_string=_get(row, "FormatString") or None,
                format_string_expression=format_strings.get(_get(row, "FormatStringDefinitionID")),
                display_folder=_get(row, "DisplayFolder") or None,
                is_hidden=_bool(_get(row, "IsHidden")),
                description=_get(row, "Description") or None,
            )
        )

    hierarchies_by_id: dict[Any, Hierarchy] = {}
    for row in hierarchy_rows:
        table = tables_by_id.get(_get(row, "TableID"))
        if table is None:
            continue
        hierarchy = Hierarchy(name=_get(row, "Name", default=""), is_hidden=_bool(_get(row, "IsHidden")))
        hierarchies_by_id[_get(row, "ID")] = hierarchy
        table.hierarchies.append(hierarchy)
    for row in sorted(level_rows, key=lambda r: _get(r, "Ordinal", default=0) or 0):
        hierarchy = hierarchies_by_id.get(_get(row, "HierarchyID"))
        if hierarchy is None:
            continue
        level_column = columns_by_id.get(_get(row, "ColumnID"))
        hierarchy.levels.append(
            HierarchyLevel(
                name=_get(row, "Name", default=""),
                column=level_column.name if level_column else None,
            )
        )

    for row in partition_rows:
        table = tables_by_id.get(_get(row, "TableID"))
        if table is None:
            continue
        table.partitions.append(
            Partition(
                name=_get(row, "Name", default=""),
                source_type=_enum(_PARTITION_TYPES, _get(row, "Type"), "type"),
                mode=_enum(_PARTITION_MODES, _get(row, "Mode"), "mode"),
                expression=_get(row, "QueryDefinition") or _get(row, "Expression") or None,
            )
        )

    # ---- relationships (columns in them are never unused, §5.3) ----------
    for index, row in enumerate(relationship_rows):
        from_column = columns_by_id.get(_get(row, "FromColumnID"))
        to_column = columns_by_id.get(_get(row, "ToColumnID"))
        from_table = tables_by_id.get(_get(row, "FromTableID"))
        to_table = tables_by_id.get(_get(row, "ToTableID"))
        model.relationships.append(
            Relationship(
                name=_get(row, "Name") or f"Relationship{index + 1}",
                from_table=from_table.name if from_table else "",
                from_column=from_column.name if from_column else "",
                to_table=to_table.name if to_table else "",
                to_column=to_column.name if to_column else "",
                is_active=_bool(_get(row, "IsActive", default=True)),
                to_cardinality=_enum(_CARDINALITY, _get(row, "ToCardinality"), "cardinality"),
                cross_filtering=_enum(
                    _CROSS_FILTERING, _get(row, "CrossFilteringBehavior"), "crossFiltering"
                ),
            )
        )

    # ---- RLS (filter expressions make columns used, §5.3) ----------------
    roles_by_id: dict[Any, Role] = {}
    for row in role_rows:
        role = Role(
            name=_get(row, "Name", default=""),
            model_permission=_enum(_MODEL_PERMISSIONS, _get(row, "ModelPermission"), "permission"),
        )
        roles_by_id[_get(row, "ID")] = role
        model.roles.append(role)
    for row in permission_rows:
        role = roles_by_id.get(_get(row, "RoleID"))
        table = tables_by_id.get(_get(row, "TableID"))
        if role is not None and table is not None:
            role.table_permissions[table.name] = _get(row, "FilterExpression") or ""

    for row in expression_rows:
        model.expressions.append(
            NamedExpression(name=_get(row, "Name", default=""), expression=_get(row, "Expression") or "")
        )

    # ---- calculation groups (wildcard dependency semantics, §5.3) --------
    groups_by_id: dict[Any, CalculationGroup] = {}
    for row in calc_group_rows:
        table = tables_by_id.get(_get(row, "TableID"))
        if table is None:
            continue
        group = CalculationGroup(precedence=_get(row, "Precedence"))
        groups_by_id[_get(row, "ID")] = group
        table.calculation_group = group
    for row in sorted(calc_item_rows, key=lambda r: _get(r, "Ordinal", default=0) or 0):
        group = groups_by_id.get(_get(row, "CalculationGroupID"))
        if group is None:
            continue
        group.items.append(
            CalculationItem(
                name=_get(row, "Name", default=""),
                expression=_get(row, "Expression") or "",
                ordinal=_get(row, "Ordinal"),
                format_string_expression=format_strings.get(_get(row, "FormatStringDefinitionID")),
            )
        )

    return model


def read_calc_dependencies(
    executor: DmvExecutor, *, warnings: list[str] | None = None
) -> list[CalcDependency]:
    """DISCOVER_CALC_DEPENDENCY — the single most valuable query (§4.4).

    This is the engine's own dependency resolution; the DAX parser
    (Milestone 3) supplements it for report-level measures and cross-checks
    it, never overrides it.
    """
    warnings = warnings if warnings is not None else []
    rows = _query(executor, "DISCOVER_CALC_DEPENDENCY", warnings)
    dependencies: list[CalcDependency] = []
    for row in rows:
        dependencies.append(
            CalcDependency(
                object_type=str(_get(row, "OBJECT_TYPE", default="")),
                table=_get(row, "TABLE") or None,
                object=str(_get(row, "OBJECT", default="")),
                expression=_get(row, "EXPRESSION") or None,
                referenced_object_type=str(_get(row, "REFERENCED_OBJECT_TYPE", default="")),
                referenced_table=_get(row, "REFERENCED_TABLE") or None,
                referenced_object=str(_get(row, "REFERENCED_OBJECT", default="")),
            )
        )
    return dependencies


def list_databases(executor: DmvExecutor, *, warnings: list[str] | None = None) -> list[str]:
    """Database names on the instance — Desktop hosts a single GUID-named
    database (§4.2), so callers usually take the only entry."""
    warnings = warnings if warnings is not None else []
    rows = _query(executor, "DBSCHEMA_CATALOGS", warnings)
    return [str(_get(row, "CATALOG_NAME", default="")) for row in rows if _get(row, "CATALOG_NAME")]
