"""Tolerant TMDL reader for PBIP semantic-model `definition/` folders.

Parses the subset of TMDL needed for the model inventory (spec C1):
tables, columns, measures, hierarchies, partitions, relationships, roles
and shared M expressions. Unknown keywords and properties are ignored
rather than rejected — real-world TMDL grows properties faster than any
parser tracks them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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

_KEYWORDS = {
    "database",
    "model",
    "table",
    "column",
    "measure",
    "hierarchy",
    "level",
    "partition",
    "relationship",
    "role",
    "expression",
    "tablePermission",
    "calculationGroup",
    "calculationItem",
    "annotation",
    "variation",
    "extendedProperty",
    "ref",
}


@dataclass
class _Node:
    """One declaration or property line plus its indented children."""

    keyword: str | None  # None for `key: value` property lines
    name: str = ""
    inline_value: str | None = None  # text after `=` on the same line
    expression: str | None = None  # multi-line body following a trailing `=`
    properties: dict[str, str] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    children: list["_Node"] = field(default_factory=list)


def _indent_of(line: str) -> int:
    count = 0
    for char in line:
        if char == "\t":
            count += 1
        elif char == " ":
            count += 1
        else:
            break
    return count


def _split_name(text: str) -> tuple[str, str]:
    """Split `'Quoted Name' rest` or `Bare rest` → (name, rest)."""
    text = text.strip()
    if text.startswith("'"):
        end = 1
        while end < len(text):
            if text[end] == "'" and not (end + 1 < len(text) and text[end + 1] == "'"):
                break
            end += 2 if text[end] == "'" else 1
        return text[1:end].replace("''", "'"), text[end + 1 :].strip()
    match = re.match(r"[^\s=]+", text)
    if not match:
        return "", text
    return match.group(0), text[match.end() :].strip()


def parse_tmdl(text: str) -> list[_Node]:
    """Parse one TMDL document into a node tree."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("///"):
            continue
        lines.append((_indent_of(raw), raw.strip()))

    position = 0

    def parse_level(indent: int) -> list[_Node]:
        nonlocal position
        nodes: list[_Node] = []
        while position < len(lines):
            line_indent, content = lines[position]
            if line_indent < indent:
                break
            if line_indent > indent:
                # stray deeper line (e.g. relaxed formatting) — attach to the
                # previous node by re-parsing at its own indent
                if nodes:
                    nodes[-1].children.extend(parse_level(line_indent))
                    continue
                position += 1
                continue
            # `column Year` is a declaration; `column: Year` (a hierarchy
            # level's property) is not — the bare word must match exactly
            first_word = content.split(None, 1)[0]
            if first_word in _KEYWORDS:
                position += 1
                node = _parse_declaration(first_word, content, indent)
                nodes.append(node)
            else:
                node = _parse_property_line(content)
                position += 1
                if node.inline_value is None and content.rstrip().endswith("="):
                    # a property expression body (`source =`) has no sibling
                    # properties below it — everything deeper is expression
                    node.expression = _consume_expression(indent)
                nodes.append(node)
        return nodes

    def _parse_declaration(keyword: str, content: str, indent: int) -> _Node:
        nonlocal position
        rest = content[len(keyword) :].strip()
        if keyword == "ref":  # `ref table X` — pointer lines in model.tmdl
            return _Node(keyword="ref", name=rest)
        name, remainder = _split_name(rest)
        node = _Node(keyword=keyword, name=name)
        if remainder.startswith("="):
            value = remainder[1:].strip()
            if value:
                node.inline_value = value
            else:
                node.expression = _consume_expression(indent + 1)
        _attach_children(node, parse_level(indent + 1))
        return node

    def _consume_expression(min_indent: int) -> str:
        """Consume the indented block after a trailing `=` as raw text.

        Expression bodies are indented deeper than sibling properties
        (declaration indent + 2 in files Desktop writes); properties sit at
        declaration indent + 1. Only lines deeper than min_indent belong to
        the expression.
        """
        nonlocal position
        collected: list[str] = []
        while position < len(lines):
            line_indent, content = lines[position]
            if line_indent <= min_indent:
                break
            collected.append(content)
            position += 1
        return "\n".join(collected)

    def _parse_property_line(content: str) -> _Node:
        key_value = re.match(r"^([A-Za-z_][\w]*)\s*:\s*(.*)$", content)
        if key_value:
            return _Node(keyword=None, name=key_value.group(1), inline_value=key_value.group(2))
        key_expr = re.match(r"^([A-Za-z_][\w]*)\s*=\s*(.*)$", content)
        if key_expr:
            value = key_expr.group(2).strip()
            return _Node(keyword=None, name=key_expr.group(1), inline_value=value or None)
        return _Node(keyword=None, name=content)  # boolean flag, e.g. isHidden

    roots = parse_level(0)
    return roots


def _attach_children(node: _Node, children: list[_Node]) -> None:
    for child in children:
        if child.keyword is not None:
            node.children.append(child)
        elif child.inline_value is not None or child.expression is not None:
            node.properties[child.name] = child.expression or child.inline_value or ""
        else:
            node.flags.add(child.name)


# ---------------------------------------------------------------------------
# Mapping node trees onto the schema
# ---------------------------------------------------------------------------


def read_definition_dir(definition_dir: Path, warnings: list[str]) -> Model:
    model = Model(name=definition_dir.parent.name)

    database_file = definition_dir / "database.tmdl"
    if database_file.exists():
        for node in parse_tmdl(database_file.read_text(encoding="utf-8-sig")):
            if node.keyword == "database" and node.name:
                model.name = node.name

    model_file = definition_dir / "model.tmdl"
    if model_file.exists():
        for node in parse_tmdl(model_file.read_text(encoding="utf-8-sig")):
            if node.keyword == "model":
                model.culture = node.properties.get("culture")

    tables_dir = definition_dir / "tables"
    if tables_dir.is_dir():
        for table_file in sorted(tables_dir.glob("*.tmdl")):
            for node in parse_tmdl(table_file.read_text(encoding="utf-8-sig")):
                if node.keyword == "table":
                    model.tables.append(_build_table(node))

    relationships_file = definition_dir / "relationships.tmdl"
    if relationships_file.exists():
        for node in parse_tmdl(relationships_file.read_text(encoding="utf-8-sig")):
            if node.keyword == "relationship":
                model.relationships.append(_build_relationship(node))

    roles_dir = definition_dir / "roles"
    if roles_dir.is_dir():
        for role_file in sorted(roles_dir.glob("*.tmdl")):
            for node in parse_tmdl(role_file.read_text(encoding="utf-8-sig")):
                if node.keyword == "role":
                    model.roles.append(_build_role(node))

    expressions_file = definition_dir / "expressions.tmdl"
    if expressions_file.exists():
        for node in parse_tmdl(expressions_file.read_text(encoding="utf-8-sig")):
            if node.keyword == "expression":
                model.expressions.append(
                    NamedExpression(name=node.name, expression=node.expression or node.inline_value or "")
                )

    if not model.tables:
        warnings.append(f"TMDL definition at {definition_dir} yielded no tables")
    return model


def _build_table(node: _Node) -> Table:
    table = Table(
        name=node.name,
        is_hidden="isHidden" in node.flags,
        data_category=node.properties.get("dataCategory"),
    )
    for child in node.children:
        if child.keyword == "column":
            table.columns.append(_build_column(child))
        elif child.keyword == "measure":
            table.measures.append(
                Measure(
                    name=child.name,
                    expression=child.expression or child.inline_value or "",
                    format_string=child.properties.get("formatString"),
                    display_folder=child.properties.get("displayFolder"),
                    is_hidden="isHidden" in child.flags,
                )
            )
        elif child.keyword == "hierarchy":
            table.hierarchies.append(
                Hierarchy(
                    name=child.name,
                    is_hidden="isHidden" in child.flags,
                    levels=[
                        HierarchyLevel(name=level.name, column=level.properties.get("column"))
                        for level in child.children
                        if level.keyword == "level"
                    ],
                )
            )
        elif child.keyword == "partition":
            table.partitions.append(
                Partition(
                    name=child.name,
                    source_type=child.inline_value,
                    mode=child.properties.get("mode"),
                    expression=child.properties.get("source"),
                )
            )
    return table


def _build_column(node: _Node) -> Column:
    return Column(
        name=node.name,
        data_type=node.properties.get("dataType"),
        is_hidden="isHidden" in node.flags,
        is_key="isKey" in node.flags,
        is_calculated=bool(node.inline_value or node.expression),
        expression=node.expression or node.inline_value,
        source_column=node.properties.get("sourceColumn"),
        sort_by_column=node.properties.get("sortByColumn"),
        summarize_by=node.properties.get("summarizeBy"),
        format_string=node.properties.get("formatString"),
        data_category=node.properties.get("dataCategory"),
    )


def _split_column_path(text: str) -> tuple[str, str]:
    """`'T 1'.Col` or `Table.Column` → (table, column)."""
    text = text.strip()
    if text.startswith("'"):
        name, rest = _split_name(text)
        return name, rest.lstrip(".").strip().strip("'")
    table, _, column = text.rpartition(".")
    return table.strip().strip("'"), column.strip().strip("'")


def _build_relationship(node: _Node) -> Relationship:
    from_table, from_column = _split_column_path(node.properties.get("fromColumn", "."))
    to_table, to_column = _split_column_path(node.properties.get("toColumn", "."))
    return Relationship(
        name=node.name,
        from_table=from_table,
        from_column=from_column,
        to_table=to_table,
        to_column=to_column,
        is_active=node.properties.get("isActive", "true").lower() != "false",
        to_cardinality=node.properties.get("toCardinality"),
        cross_filtering=node.properties.get("crossFilteringBehavior"),
    )


def _build_role(node: _Node) -> Role:
    role = Role(name=node.name, model_permission=node.properties.get("modelPermission"))
    for child in node.children:
        if child.keyword == "tablePermission":
            role.table_permissions[child.name] = child.expression or child.inline_value or ""
    return role
