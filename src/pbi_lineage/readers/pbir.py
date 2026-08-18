"""Parser for the PBIR report definition (build spec §5.2).

Each visual lives in its own `visual.json` under
`definition/pages/<page>/visuals/<visual>/`; report-level measures live in
`definition/reportExtensions.json`. The field-reference shapes are the same
as the legacy layout's, so the shared deep scan in `refs` does the work —
here we only route sub-documents to the right scope labels.
"""

from __future__ import annotations

import json
from pathlib import Path

from pbi_lineage.readers.refs import collect_aliases, dedupe, scan_refs
from pbi_lineage.schema import FieldReference, Page, ReportLayout, ReportLevelMeasure, Visual


def _load_json(path: Path, warnings: list[str]) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        warnings.append(f"{path}: {exc}")
        return None


def read_report_definition(report_dir: Path, *, name: str | None, warnings: list[str]) -> ReportLayout:
    definition = report_dir / "definition"
    report = ReportLayout(name=name, source_format="pbir")

    report_json = (
        _load_json(definition / "report.json", warnings) if (definition / "report.json").exists() else None
    )
    if report_json is not None:
        refs: list[FieldReference] = []
        scan_refs(
            report_json.get("filterConfig"),
            aliases={},
            scope="report:filter",
            path="report.json.filterConfig",
            out=refs,
        )
        report.filters = dedupe(refs)

    extensions_file = definition / "reportExtensions.json"
    if extensions_file.exists():
        extensions = _load_json(extensions_file, warnings) or {}
        report.report_level_measures = _parse_report_extensions(extensions)

    pages_dir = definition / "pages"
    page_order = _page_order(pages_dir, warnings)
    for ordinal, page_name in enumerate(page_order):
        page_dir = pages_dir / page_name
        page_json = _load_json(page_dir / "page.json", warnings) or {}
        page = Page(
            name=page_json.get("name", page_name),
            display_name=page_json.get("displayName"),
            ordinal=ordinal,
            is_hidden=page_json.get("visibility") in ("HiddenInViewMode", 1),
        )
        page_refs: list[FieldReference] = []
        scan_refs(
            page_json.get("filterConfig"),
            aliases={},
            scope="page:filter",
            path=f"pages/{page_name}/page.json.filterConfig",
            out=page_refs,
        )
        page.filters = dedupe(page_refs)

        visuals_dir = page_dir / "visuals"
        if visuals_dir.is_dir():
            for visual_dir in sorted(p for p in visuals_dir.iterdir() if p.is_dir()):
                visual_json = _load_json(visual_dir / "visual.json", warnings)
                if visual_json is not None:
                    page.visuals.append(
                        _parse_visual(visual_json, f"pages/{page_name}/visuals/{visual_dir.name}")
                    )
        report.pages.append(page)
    return report


def _page_order(pages_dir: Path, warnings: list[str]) -> list[str]:
    if not pages_dir.is_dir():
        return []
    on_disk = sorted(p.name for p in pages_dir.iterdir() if p.is_dir())
    pages_json = pages_dir / "pages.json"
    if pages_json.exists():
        meta = _load_json(pages_json, warnings) or {}
        ordered = [p for p in meta.get("pageOrder", []) if p in on_disk]
        ordered.extend(p for p in on_disk if p not in ordered)
        return ordered
    return on_disk


def _parse_visual(visual_json: dict, path: str) -> Visual:
    inner = visual_json.get("visual") or {}
    aliases = collect_aliases(visual_json)
    refs: list[FieldReference] = []

    query = inner.get("query") or {}
    scan_refs(
        query.get("queryState"),
        aliases=aliases,
        scope="visual:projection",
        path=f"{path}/visual.json.visual.query.queryState",
        out=refs,
    )
    scan_refs(
        query.get("sortDefinition"),
        aliases=aliases,
        scope="visual:sort",
        path=f"{path}/visual.json.visual.query.sortDefinition",
        out=refs,
    )
    for objects_key in ("objects", "visualContainerObjects"):
        scan_refs(
            inner.get(objects_key),
            aliases=aliases,
            scope="visual:formatting",
            path=f"{path}/visual.json.visual.{objects_key}",
            out=refs,
        )
    scan_refs(
        visual_json.get("filterConfig"),
        aliases=aliases,
        scope="visual:filter",
        path=f"{path}/visual.json.filterConfig",
        out=refs,
    )
    # deep-scan safety net over everything not already routed (§5.3:
    # custom visuals hide references in non-standard places)
    handled_inner = {"query", "objects", "visualContainerObjects"}
    for key, value in inner.items():
        if key not in handled_inner:
            scan_refs(
                value,
                aliases=aliases,
                scope="visual:other",
                path=f"{path}/visual.json.visual.{key}",
                out=refs,
            )

    position = visual_json.get("position") or {}
    return Visual(
        name=str(visual_json.get("name", "")),
        visual_type=inner.get("visualType"),
        x=position.get("x"),
        y=position.get("y"),
        width=position.get("width"),
        height=position.get("height"),
        is_hidden=bool(visual_json.get("isHidden", False)),
        sync_group=(inner.get("syncGroup") or {}).get("groupName"),
        references=dedupe(refs),
    )


def _parse_report_extensions(extensions: dict) -> list[ReportLevelMeasure]:
    measures: list[ReportLevelMeasure] = []
    for entity in extensions.get("entities", []) or []:
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
