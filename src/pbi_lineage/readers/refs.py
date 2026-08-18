"""Field-reference extraction shared by the legacy-layout and PBIR readers.

The report formats encode field references as small dict shapes:

    {"Column":  {"Expression": {"SourceRef": {...}}, "Property": "Qty"}}
    {"Measure": {"Expression": {"SourceRef": {...}}, "Property": "Total"}}
    {"HierarchyLevel": {"Expression": {"Hierarchy": {...}}, "Level": "Year"}}

`SourceRef` carries either `{"Entity": "Sales"}` (a table name directly —
typical in filters and PBIR) or `{"Source": "s"}` (an alias declared in a
`From` array — typical in prototypeQuery). The scan below walks arbitrary
JSON, resolves aliases, and — critically for the §5.3 checklist — decodes
JSON-in-JSON: any string value that parses as JSON is recursed into, so
references hidden in escaped `config`/`filters`/`expr` strings and inside
custom-visual blobs are still found.
"""

from __future__ import annotations

import json
from typing import Any

from pbi_lineage.schema import FieldReference, RefKind

_REF_KEYS = {"Column": RefKind.COLUMN, "Measure": RefKind.MEASURE}


def loads_maybe(value: Any) -> Any:
    """Return `value` with one level of JSON-string wrapping removed, if any."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[":
            try:
                return json.loads(stripped)
            except (ValueError, RecursionError):
                return None
        return None
    return value


def collect_aliases(node: Any, aliases: dict[str, str] | None = None) -> dict[str, str]:
    """Gather every `From: [{"Name": alias, "Entity": table}]` mapping in `node`."""
    if aliases is None:
        aliases = {}
    if isinstance(node, dict):
        entries = node.get("From")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("Name") and entry.get("Entity"):
                    aliases[str(entry["Name"])] = str(entry["Entity"])
        for value in node.values():
            collect_aliases(value, aliases)
    elif isinstance(node, list):
        for value in node:
            collect_aliases(value, aliases)
    elif isinstance(node, str):
        decoded = loads_maybe(node)
        if decoded is not None:
            collect_aliases(decoded, aliases)
    return aliases


def _resolve_source(expression: Any, aliases: dict[str, str]) -> str | None:
    if not isinstance(expression, dict):
        return None
    source_ref = expression.get("SourceRef")
    if not isinstance(source_ref, dict):
        return None
    if "Entity" in source_ref:
        return str(source_ref["Entity"])
    if "Source" in source_ref:
        return aliases.get(str(source_ref["Source"]))
    return None


def scan_refs(
    node: Any,
    *,
    aliases: dict[str, str],
    scope: str,
    path: str,
    out: list[FieldReference],
) -> None:
    """Append every field reference found under `node` to `out`."""
    if isinstance(node, dict):
        for key, kind in _REF_KEYS.items():
            inner = node.get(key)
            if isinstance(inner, dict) and "Property" in inner:
                out.append(
                    FieldReference(
                        table=_resolve_source(inner.get("Expression"), aliases),
                        name=str(inner["Property"]),
                        kind=kind,
                        scope=scope,
                        evidence=f"{path}.{key}",
                    )
                )
        level = node.get("HierarchyLevel")
        if isinstance(level, dict) and "Level" in level:
            hierarchy = level.get("Expression", {})
            hierarchy = hierarchy.get("Hierarchy", {}) if isinstance(hierarchy, dict) else {}
            out.append(
                FieldReference(
                    table=_resolve_source(hierarchy.get("Expression"), aliases),
                    name=f"{hierarchy.get('Hierarchy', '?')}.{level['Level']}",
                    kind=RefKind.HIERARCHY_LEVEL,
                    scope=scope,
                    evidence=f"{path}.HierarchyLevel",
                )
            )
        for key, value in node.items():
            scan_refs(value, aliases=aliases, scope=scope, path=f"{path}.{key}", out=out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            scan_refs(value, aliases=aliases, scope=scope, path=f"{path}[{index}]", out=out)
    elif isinstance(node, str):
        decoded = loads_maybe(node)
        if decoded is not None:
            scan_refs(decoded, aliases=aliases, scope=scope, path=f"{path}!json", out=out)


def dedupe(refs: list[FieldReference]) -> list[FieldReference]:
    """Drop exact duplicates, preserving order and distinct evidence paths."""
    seen: set[FieldReference] = set()
    out: list[FieldReference] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out
