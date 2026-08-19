"""Parser for the legacy `Report/Layout` blob (build spec §5.1).

The layout is JSON-in-JSON: `config` and `filters` at report, section and
visual-container level are strings containing escaped JSON. Everything is
parsed recursively; the §5.3 hiding places are covered by a combination of
targeted passes (prototypeQuery, orderBy, objects/vcObjects, filters) and a
generic deep scan as the safety net for custom visuals and unknown shapes.
"""

from __future__ import annotations

from typing import Any

from pbi_lineage.readers.refs import collect_aliases, dedupe, loads_maybe, scan_refs
from pbi_lineage.schema import FieldReference, Page, ReportLayout, ReportLevelMeasure, Visual


def parse_layout(layout: dict, *, name: str | None = None, warnings: list[str] | None = None) -> ReportLayout:
    warnings = warnings if warnings is not None else []
    report = ReportLayout(name=name, source_format="legacy")

    report.filters = _parse_filters(layout.get("filters"), scope="report:filter", path="layout.filters")

    config = loads_maybe(layout.get("config"))
    if isinstance(config, dict):
        report.report_level_measures = _parse_model_extensions(config)
        # bookmarks can pin filter state on fields not otherwise visible (§5.3)
        bookmark_refs: list[FieldReference] = []
        scan_refs(
            config.get("bookmarks"),
            aliases={},
            scope="report:bookmark",
            path="layout.config.bookmarks",
            out=bookmark_refs,
        )
        report.filters = dedupe(report.filters + bookmark_refs)
    elif layout.get("config") is not None:
        warnings.append("legacy layout: report config string did not parse as JSON")

    for index, section in enumerate(layout.get("sections", [])):
        report.pages.append(_parse_section(section, index, warnings))
    return report


def _parse_section(section: dict, index: int, warnings: list[str]) -> Page:
    config = loads_maybe(section.get("config")) or {}
    page = Page(
        name=str(section.get("name", f"section{index}")),
        display_name=section.get("displayName"),
        ordinal=section.get("ordinal", index),
        is_hidden=config.get("visibility", 0) == 1,
        filters=_parse_filters(
            section.get("filters"), scope="page:filter", path=f"sections[{index}].filters"
        ),
    )
    for vc_index, container in enumerate(section.get("visualContainers", [])):
        page.visuals.append(
            _parse_visual_container(container, f"sections[{index}].visualContainers[{vc_index}]", warnings)
        )
    return page


def _parse_visual_container(container: dict, path: str, warnings: list[str]) -> Visual:
    config = loads_maybe(container.get("config"))
    if config is None:
        config = {}
        if container.get("config") is not None:
            warnings.append(f"{path}: visual config string did not parse as JSON")
    single = config.get("singleVisual") or {}

    # aliases declared anywhere in the container (prototypeQuery From,
    # query/dataTransforms From) apply to Source-style SourceRefs
    aliases = collect_aliases(container)

    refs: list[FieldReference] = []
    prototype = single.get("prototypeQuery")
    if isinstance(prototype, dict):
        scan_refs(
            prototype.get("Select"),
            aliases=aliases,
            scope="visual:projection",
            path=f"{path}.config.singleVisual.prototypeQuery.Select",
            out=refs,
        )
        # sort-by may reference a field that is not projected (§5.3)
        scan_refs(
            prototype.get("OrderBy"),
            aliases=aliases,
            scope="visual:sort",
            path=f"{path}.config.singleVisual.prototypeQuery.OrderBy",
            out=refs,
        )
    scan_refs(
        single.get("orderBy"),
        aliases=aliases,
        scope="visual:sort",
        path=f"{path}.config.singleVisual.orderBy",
        out=refs,
    )
    # conditional formatting, data labels, reference lines … (§5.3)
    for objects_key in ("objects", "vcObjects"):
        scan_refs(
            single.get(objects_key),
            aliases=aliases,
            scope="visual:formatting",
            path=f"{path}.config.singleVisual.{objects_key}",
            out=refs,
        )
    refs.extend(_parse_filters(container.get("filters"), scope="visual:filter", path=f"{path}.filters"))
    # The executed query carries `Where` clauses that `prototypeQuery` does
    # not — a field filtered there and projected nowhere would otherwise read
    # as unused (§5.3: filters on fields not shown in the visual).
    query = loads_maybe(container.get("query"))
    if query is None and isinstance(container.get("query"), dict):
        query = container["query"]
    if query is not None:
        query_aliases = collect_aliases(query) or aliases
        for section_key, scope in (
            ("Where", "visual:filter"),
            ("Select", "visual:projection"),
            ("OrderBy", "visual:sort"),
            ("GroupBy", "visual:projection"),
        ):
            scan_refs(
                _query_section(query, section_key),
                aliases=query_aliases,
                scope=scope,
                path=f"{path}.query.{section_key}",
                out=refs,
            )
    transforms = loads_maybe(container.get("dataTransforms"))
    if transforms is not None:
        scan_refs(
            transforms,
            aliases=aliases,
            scope="visual:other",
            path=f"{path}.dataTransforms",
            out=refs,
        )
    # generic deep scan over whatever remains — custom visuals hide
    # references in non-standard places (§5.3)
    handled = {"prototypeQuery", "orderBy", "objects", "vcObjects", "projections", "columnProperties"}
    for key, value in single.items():
        if key not in handled:
            scan_refs(
                value,
                aliases=aliases,
                scope="visual:other",
                path=f"{path}.config.singleVisual.{key}",
                out=refs,
            )

    display = single.get("display") or {}
    sync_group = single.get("syncGroup") or {}
    return Visual(
        name=str(config.get("name", "")),
        visual_type=single.get("visualType"),
        title=_literal_title(single),
        x=container.get("x"),
        y=container.get("y"),
        width=container.get("width"),
        height=container.get("height"),
        is_hidden=display.get("mode") == "hidden",
        sync_group=sync_group.get("groupName"),
        references=dedupe(refs),
    )


def _literal_title(single: dict) -> str | None:
    """The visual's typed-in title, if it has one.

    "shown in the card “Total sales this year”" tells a reader where to
    look; "shown in the card1652434605854" does not. Only a literal counts
    — a title bound to an expression is not a name we can quote.
    """
    entries = ((single.get("vcObjects") or {}).get("title") or [])
    for entry in entries:
        expression = ((entry.get("properties") or {}).get("text") or {}).get("expr") or {}
        value = (expression.get("Literal") or {}).get("Value")
        if isinstance(value, str) and value.strip():
            return value.strip().strip("'").strip() or None
    return None


def _parse_filters(raw: Any, *, scope: str, path: str) -> list[FieldReference]:
    decoded = loads_maybe(raw)
    if decoded is None:
        decoded = raw if isinstance(raw, (list, dict)) else None
    refs: list[FieldReference] = []
    scan_refs(decoded, aliases={}, scope=scope, path=path, out=refs)
    return dedupe(refs)


def _parse_model_extensions(config: dict) -> list[ReportLevelMeasure]:
    """Report-level measures: config → modelExtensions[] → entities[] →
    measures[] (spec §5.4.2). They exist only in the report file."""
    measures: list[ReportLevelMeasure] = []
    for extension in config.get("modelExtensions", []) or []:
        for entity in extension.get("entities", []) or []:
            table = entity.get("extends") or entity.get("name")
            for measure in entity.get("measures", []) or []:
                measures.append(
                    ReportLevelMeasure(
                        name=str(measure.get("name", "")),
                        table=table,
                        expression=measure.get("expression", ""),
                        is_hidden=bool(measure.get("hidden", False)),
                    )
                )
    return measures


def _query_section(query, key):
    """Pull one section out of a visual's executed query, whether it is a bare
    `Query` object or wrapped in `Commands[].SemanticQueryDataShapeCommand`."""
    found = []

    def visit(node):
        if isinstance(node, dict):
            if key in node and ("From" in node or "Select" in node or "Where" in node):
                found.append(node[key])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(query)
    return found
