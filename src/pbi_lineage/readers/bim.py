"""TMSL (`model.bim`) fallback reader for PBIP projects that predate TMDL."""

from __future__ import annotations

import json
from pathlib import Path

from pbi_lineage.schema import (
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


def read_model_bim(path: Path, warnings: list[str]) -> Model:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        warnings.append(f"{path}: {exc}")
        return Model(name=path.parent.name)

    body = document.get("model", document)
    model = Model(name=document.get("name", path.parent.name), culture=body.get("culture"))

    for table_json in body.get("tables", []) or []:
        model.tables.append(_build_table(table_json))
    for index, rel in enumerate(body.get("relationships", []) or []):
        model.relationships.append(
            Relationship(
                name=str(rel.get("name", f"Relationship{index + 1}")),
                from_table=rel.get("fromTable", ""),
                from_column=rel.get("fromColumn", ""),
                to_table=rel.get("toTable", ""),
                to_column=rel.get("toColumn", ""),
                is_active=rel.get("isActive", True),
                to_cardinality=rel.get("toCardinality"),
                cross_filtering=rel.get("crossFilteringBehavior"),
            )
        )
    for role_json in body.get("roles", []) or []:
        role = Role(name=role_json.get("name", ""), model_permission=role_json.get("modelPermission"))
        for permission in role_json.get("tablePermissions", []) or []:
            role.table_permissions[permission.get("name", "")] = permission.get("filterExpression", "")
        model.roles.append(role)
    for expr in body.get("expressions", []) or []:
        model.expressions.append(
            NamedExpression(name=expr.get("name", ""), expression=_expr_text(expr.get("expression")))
        )
    return model


def _expr_text(expression) -> str:
    if isinstance(expression, list):
        return "\n".join(str(line) for line in expression)
    return str(expression or "")


def _build_table(table_json: dict) -> Table:
    table = Table(
        name=table_json.get("name", ""),
        is_hidden=table_json.get("isHidden", False),
        data_category=table_json.get("dataCategory"),
    )
    for col in table_json.get("columns", []) or []:
        table.columns.append(
            Column(
                name=col.get("name", ""),
                data_type=col.get("dataType"),
                is_hidden=col.get("isHidden", False),
                is_key=col.get("isKey", False),
                is_calculated=col.get("type") == "calculated",
                expression=_expr_text(col.get("expression")) if col.get("expression") else None,
                source_column=col.get("sourceColumn"),
                sort_by_column=col.get("sortByColumn"),
                summarize_by=col.get("summarizeBy"),
                format_string=col.get("formatString"),
                data_category=col.get("dataCategory"),
            )
        )
    for measure in table_json.get("measures", []) or []:
        table.measures.append(
            Measure(
                name=measure.get("name", ""),
                expression=_expr_text(measure.get("expression")),
                format_string=measure.get("formatString"),
                display_folder=measure.get("displayFolder"),
                is_hidden=measure.get("isHidden", False),
                description=measure.get("description"),
            )
        )
    for hierarchy in table_json.get("hierarchies", []) or []:
        table.hierarchies.append(
            Hierarchy(
                name=hierarchy.get("name", ""),
                is_hidden=hierarchy.get("isHidden", False),
                levels=[
                    HierarchyLevel(name=level.get("name", ""), column=level.get("column"))
                    for level in hierarchy.get("levels", []) or []
                ],
            )
        )
    for partition in table_json.get("partitions", []) or []:
        source = partition.get("source", {}) or {}
        table.partitions.append(
            Partition(
                name=partition.get("name", ""),
                source_type=source.get("type"),
                mode=partition.get("mode"),
                expression=_expr_text(source.get("expression")) if source.get("expression") else None,
            )
        )
    return table
