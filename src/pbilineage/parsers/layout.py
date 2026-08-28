"""Parse a report's field bindings out of an exported PBIX.

No Admin API exposes visual layout, so the report layer is reconstructed from
the PBIX the Export API produces: unzip it, read `Report/Layout` (UTF-16 JSON,
with several levels of JSON-inside-a-string), and pull out every model object
a visual, a filter or a conditional-formatting rule binds to.

Bindings found this way are `resolved`: they are literal references in the
report definition, not something we inferred.

The newer PBIR folder layout (`definition/pages/**/visual.json`) is handled
too, since exports from Fabric-era workspaces can come back in that shape.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from pbilineage.models import PageSpec, VisualFieldSpec, VisualSpec

__all__ = ["parse_layout", "parse_pbix_layout", "read_layout_json"]

#: Power BI aggregation function codes as they appear in prototypeQuery
AGGREGATION_FUNCTIONS = {
    0: "Sum",
    1: "Avg",
    2: "DistinctCount",
    3: "Min",
    4: "Max",
    5: "Count",
    6: "Median",
    7: "StdDev",
    8: "Var",
}

_LAYOUT_MEMBERS = ("Report/Layout", "Report\\Layout")


def _loads(value: Any) -> Any:
    """Layout nests JSON inside JSON strings; decode transparently."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def read_layout_json(pbix_path: str | Path) -> dict[str, Any] | None:
    """Read and decode `Report/Layout` from a PBIX file, or None if absent."""
    path = Path(pbix_path)
    if not path.is_file():
        return None
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        member = next((m for m in _LAYOUT_MEMBERS if m in names), None)
        if member is None:
            return _read_pbir(archive)
        raw = archive.read(member)
    for encoding in ("utf-16-le", "utf-16", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        parsed = _loads(text.lstrip("﻿"))
        if isinstance(parsed, dict):
            return parsed
    return None


def _read_pbir(archive: zipfile.ZipFile) -> dict[str, Any] | None:
    """Assemble a Layout-shaped dict from a PBIR (folder-format) report."""
    visual_files = [n for n in archive.namelist() if n.endswith("/visual.json")]
    if not visual_files:
        return None
    pages: dict[str, dict[str, Any]] = {}
    for name in sorted(visual_files):
        parts = name.split("/")
        page_name = parts[-3] if len(parts) >= 3 else "Page"
        page = pages.setdefault(page_name, {"name": page_name, "visualContainers": []})
        payload = _loads(archive.read(name).decode("utf-8", errors="replace"))
        if isinstance(payload, dict):
            page["visualContainers"].append({"config": json.dumps(payload)})
    return {"sections": list(pages.values()), "_format": "pbir"}


def parse_pbix_layout(pbix_path: str | Path) -> list[PageSpec]:
    """Convenience wrapper: PBIX file on disk -> pages with field bindings."""
    layout = read_layout_json(pbix_path)
    return parse_layout(layout) if layout else []


def parse_layout(layout: dict[str, Any] | str) -> list[PageSpec]:
    """Turn a decoded `Report/Layout` document into pages, visuals and fields."""
    document = _loads(layout) if isinstance(layout, str) else layout
    if not isinstance(document, dict):
        return []

    pages: list[PageSpec] = []
    for ordinal, section in enumerate(document.get("sections") or []):
        if not isinstance(section, dict):
            continue
        page = PageSpec(
            id=str(section.get("name") or f"page{ordinal}"),
            name=str(section.get("displayName") or section.get("name") or f"Page {ordinal + 1}"),
            ordinal=int(section.get("ordinal", ordinal) or ordinal),
        )
        page.fields.extend(_filter_fields(section.get("filters")))
        for index, container in enumerate(section.get("visualContainers") or []):
            visual = _parse_visual_container(container, index)
            if visual is not None:
                page.visuals.append(visual)
        pages.append(page)
    return pages


def _parse_visual_container(container: Any, index: int) -> VisualSpec | None:
    if not isinstance(container, dict):
        return None
    config = _loads(container.get("config")) or {}
    single = config.get("singleVisual") if isinstance(config, dict) else None
    if not isinstance(single, dict):
        # visual groups and newer shapes keep the payload one level down
        single = (config.get("visual") if isinstance(config, dict) else None) or {}

    visual = VisualSpec(
        id=str(config.get("name") or container.get("id") or f"visual{index}"),
        visual_type=str(single.get("visualType") or config.get("visualType") or "unknown"),
        title=_visual_title(single),
    )

    prototype = single.get("prototypeQuery") or single.get("query") or {}
    aliases = _entity_aliases(prototype)
    selects = _select_map(prototype, aliases)

    projections = single.get("projections")
    seen: set[tuple[str, str, str]] = set()
    #: fields a projection already bound to a real visual role
    projected: set[tuple[str, str]] = set()
    if isinstance(projections, dict):
        for role, items in projections.items():
            for item in items if isinstance(items, list) else []:
                query_ref = item.get("queryRef") if isinstance(item, dict) else None
                field = selects.get(query_ref) if query_ref else None
                if field is None and isinstance(query_ref, str):
                    field = _field_from_query_ref(query_ref)
                if field is None:
                    continue
                bound = field.model_copy(update={"role": str(role)})
                key = (bound.table.lower(), bound.field.lower(), bound.role)
                if key not in seen:
                    seen.add(key)
                    projected.add((bound.table.lower(), bound.field.lower()))
                    visual.fields.append(bound)

    # Referenced but not projected: sorts, tooltips, drillthrough. A field the
    # projections already placed in a role is not repeated under a generic one.
    for field in selects.values():
        if (field.table.lower(), field.field.lower()) in projected:
            continue
        key = (field.table.lower(), field.field.lower(), field.role)
        if key not in seen:
            seen.add(key)
            visual.fields.append(field)

    # conditional formatting and other object-level expressions
    for source in (single.get("objects"), single.get("vcObjects"), container.get("dataTransforms")):
        payload = _loads(source) if isinstance(source, str) else source
        for field in _walk_refs(payload, aliases, role="conditional_formatting"):
            key = (field.table.lower(), field.field.lower(), field.role)
            if key not in seen:
                seen.add(key)
                visual.fields.append(field)

    for field in _filter_fields(container.get("filters")) + _filter_fields(single.get("filters")):
        key = (field.table.lower(), field.field.lower(), field.role)
        if key not in seen:
            seen.add(key)
            visual.fields.append(field)

    return visual


def _visual_title(single: dict[str, Any]) -> str:
    objects = single.get("vcObjects") or {}
    titles = objects.get("title") if isinstance(objects, dict) else None
    for entry in titles if isinstance(titles, list) else []:
        expr = entry.get("properties", {}).get("text", {}).get("expr", {}) if isinstance(entry, dict) else {}
        literal = expr.get("Literal", {}).get("Value") if isinstance(expr, dict) else None
        if isinstance(literal, str):
            return literal.strip("'")
    return ""


def _entity_aliases(prototype: Any) -> dict[str, str]:
    """`From` gives `{"Name": "s", "Entity": "Sales"}` alias bindings."""
    aliases: dict[str, str] = {}
    if not isinstance(prototype, dict):
        return aliases
    for entry in prototype.get("From") or []:
        if isinstance(entry, dict) and entry.get("Name") and entry.get("Entity"):
            aliases[str(entry["Name"])] = str(entry["Entity"])
    return aliases


def _select_map(prototype: Any, aliases: dict[str, str]) -> dict[str, VisualFieldSpec]:
    """Map each `Select` entry's queryRef name to the object it binds to."""
    result: dict[str, VisualFieldSpec] = {}
    if not isinstance(prototype, dict):
        return result
    for entry in prototype.get("Select") or []:
        if not isinstance(entry, dict):
            continue
        fields = list(_walk_refs(entry, aliases, role="field"))
        if not fields:
            continue
        name = str(entry.get("Name") or "")
        field = fields[0]
        if "Aggregation" in entry:
            function = entry.get("Aggregation", {}).get("Function")
            field = field.model_copy(update={"aggregation": AGGREGATION_FUNCTIONS.get(function, "")})
        result[name or field.field] = field
    return result


def _field_from_query_ref(query_ref: str) -> VisualFieldSpec | None:
    """Fallback for `Sum(Sales.Amount)` / `Sales.Region` style refs."""
    text = query_ref.strip()
    aggregation = ""
    if "(" in text and text.endswith(")"):
        head, _, rest = text.partition("(")
        if head.strip() in AGGREGATION_FUNCTIONS.values():
            aggregation = head.strip()
            text = rest[:-1].strip()
    if "." not in text:
        return None
    table, _, field = text.rpartition(".")
    if not table or not field:
        return None
    return VisualFieldSpec(
        table=table,
        field=field,
        field_kind="unknown",
        role="field",
        aggregation=aggregation,
    )


def _walk_refs(
    payload: Any, aliases: dict[str, str], role: str, _depth: int = 0
) -> Iterable[VisualFieldSpec]:
    """Yield every Column / Measure / HierarchyLevel reference under `payload`.

    Walking generically means filters, sorts, tooltips and conditional
    formatting all work without a special case per container shape.
    """
    if _depth > 24 or payload is None:
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _walk_refs(item, aliases, role, _depth + 1)
        return
    if not isinstance(payload, dict):
        return

    for key, kind in (("Column", "column"), ("Measure", "measure")):
        node = payload.get(key)
        if isinstance(node, dict) and "Property" in node:
            entity = _source_entity(node.get("Expression"), aliases)
            if entity:
                yield VisualFieldSpec(
                    table=entity,
                    field=str(node["Property"]),
                    field_kind=kind,
                    role=role,
                )

    level = payload.get("HierarchyLevel")
    if isinstance(level, dict):
        hierarchy = level.get("Expression", {}).get("Hierarchy", {})
        entity = _source_entity(hierarchy.get("Expression"), aliases)
        if entity and level.get("Level"):
            yield VisualFieldSpec(
                table=entity,
                field=str(level["Level"]),
                field_kind="column",
                role=role,
            )

    for key, value in payload.items():
        if key in ("Column", "Measure", "HierarchyLevel"):
            continue
        if isinstance(value, str):
            nested = _loads(value)
            if isinstance(nested, (dict, list)):
                yield from _walk_refs(nested, aliases, role, _depth + 1)
            continue
        yield from _walk_refs(value, aliases, role, _depth + 1)


def _source_entity(expression: Any, aliases: dict[str, str]) -> str:
    """Resolve `{"SourceRef": {"Source": "s"}}` or `{"Entity": "Sales"}`."""
    if not isinstance(expression, dict):
        return ""
    source_ref = expression.get("SourceRef")
    if isinstance(source_ref, dict):
        if source_ref.get("Entity"):
            return str(source_ref["Entity"])
        alias = source_ref.get("Source")
        if alias:
            return aliases.get(str(alias), str(alias))
    # nested one level, e.g. Aggregation -> Expression -> Column -> Expression
    for value in expression.values():
        if isinstance(value, dict):
            found = _source_entity(value, aliases)
            if found:
                return found
    return ""


def _filter_fields(payload: Any) -> list[VisualFieldSpec]:
    parsed = _loads(payload)
    if parsed is None:
        return []
    return list(_walk_refs(parsed, {}, role="filter"))
