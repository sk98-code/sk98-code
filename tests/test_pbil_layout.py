"""Legacy Report/Layout parsing — one test per §5.3 hiding place.

Every case here is a place where a naive reader would miss a reference and
produce a false "unused" verdict.
"""

from pbi_lineage.readers.layout_legacy import parse_layout
from pbi_lineage.schema import RefKind

from .pbil_fixtures import (
    alias_ref,
    categorical_filter,
    column_expr,
    entity_ref,
    layout,
    measure_expr,
    section,
    visual_container,
)


def _prototype(select: list[dict], order_by: list[dict] | None = None) -> dict:
    query: dict = {
        "Version": 2,
        "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
        "Select": select,
    }
    if order_by is not None:
        query["OrderBy"] = order_by
    return query


def _single_visual_page(container: dict, **section_kwargs):
    doc = layout(sections=[section(visual_containers=[container], **section_kwargs)])
    return parse_layout(doc)


def test_projection_with_alias_resolution():
    container = visual_container(
        prototype_query=_prototype(
            [
                {"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "Sales.Qty"},
                {
                    "Measure": {"Expression": alias_ref("s"), "Property": "Total Sales"},
                    "Name": "Sales.Total Sales",
                },
            ]
        )
    )
    report = _single_visual_page(container)
    visual = report.pages[0].visuals[0]
    fields = visual.fields()
    assert ("Sales", "Qty", RefKind.COLUMN) in fields
    assert ("Sales", "Total Sales", RefKind.MEASURE) in fields
    assert all(ref.evidence for ref in visual.references)


def test_visual_filter_on_unprojected_field():
    # §5.3: visual-level filters, including on fields not shown in the visual
    container = visual_container(
        prototype_query=_prototype(
            [{"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "Sales.Qty"}]
        ),
        filters=[categorical_filter("f1", column_expr(entity_ref("Sales"), "Region"))],
    )
    report = _single_visual_page(container)
    visual = report.pages[0].visuals[0]
    filter_refs = [r for r in visual.references if r.scope == "visual:filter"]
    assert [(r.table, r.name) for r in filter_refs] == [("Sales", "Region")]
    projected = {r.name for r in visual.references if r.scope == "visual:projection"}
    assert "Region" not in projected


def test_conditional_formatting_fillrule_on_unprojected_measure():
    # §5.3: conditional formatting rules (objects → FillRule)
    objects = {
        "dataPoint": [
            {
                "properties": {
                    "fill": {
                        "solid": {
                            "color": {
                                "expr": {
                                    "FillRule": {
                                        "Input": measure_expr(alias_ref("s"), "Margin"),
                                        "FillRule": {"linearGradient2": {}},
                                    }
                                }
                            }
                        }
                    }
                }
            }
        ]
    }
    container = visual_container(
        prototype_query=_prototype(
            [{"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "Sales.Qty"}]
        ),
        objects=objects,
    )
    report = _single_visual_page(container)
    refs = report.pages[0].visuals[0].references
    formatting = [r for r in refs if r.scope == "visual:formatting"]
    assert [(r.table, r.name, r.kind) for r in formatting] == [("Sales", "Margin", RefKind.MEASURE)]
    assert "FillRule" in formatting[0].evidence


def test_dynamic_title_measure():
    # §5.3: dynamic titles driven by a measure
    vc_objects = {
        "title": [{"properties": {"text": {"expr": measure_expr(entity_ref("Sales"), "Title Measure")}}}]
    }
    report = _single_visual_page(visual_container(vc_objects=vc_objects))
    refs = report.pages[0].visuals[0].references
    assert [(r.table, r.name, r.scope) for r in refs] == [("Sales", "Title Measure", "visual:formatting")]


def test_orderby_references_unprojected_field():
    # §5.3: sort-by field may reference an unprojected field
    container = visual_container(
        prototype_query=_prototype(
            [{"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "Sales.Qty"}],
            order_by=[
                {
                    "Direction": 2,
                    "Expression": {"Column": {"Expression": alias_ref("s"), "Property": "OrderDate"}},
                }
            ],
        )
    )
    report = _single_visual_page(container)
    sort_refs = [r for r in report.pages[0].visuals[0].references if r.scope == "visual:sort"]
    assert [(r.table, r.name) for r in sort_refs] == [("Sales", "OrderDate")]


def test_report_level_measures_from_model_extensions():
    # §5.4.2: report-level measures exist only in the report file
    config = {
        "modelExtensions": [
            {
                "name": "extension",
                "entities": [
                    {
                        "name": "Sales",
                        "extends": "Sales",
                        "measures": [
                            {"name": "Adj Margin", "expression": "[Total Sales] * 0.9", "hidden": False},
                            {"name": "Secret", "expression": "1", "hidden": True},
                        ],
                    }
                ],
            }
        ]
    }
    doc = layout(sections=[section()], config=config)
    report = parse_layout(doc)
    assert len(report.report_level_measures) == 2
    adj = report.report_level_measures[0]
    assert (adj.name, adj.table, adj.expression, adj.is_hidden) == (
        "Adj Margin",
        "Sales",
        "[Total Sales] * 0.9",
        False,
    )
    assert report.report_level_measures[1].is_hidden


def test_slicer_on_hidden_page_is_used_not_unused():
    # §5.3: hidden pages and their slicers are used, flagged separately
    slicer = visual_container(
        visual_type="slicer",
        prototype_query=_prototype(
            [{"Column": {"Expression": alias_ref("s"), "Property": "Year"}, "Name": "Sales.Year"}]
        ),
        sync_group="YearSync",
    )
    report = _single_visual_page(slicer, hidden=True)
    page = report.pages[0]
    assert page.is_hidden
    visual = page.visuals[0]
    assert visual.sync_group == "YearSync"
    assert ("Sales", "Year", RefKind.COLUMN) in visual.fields()


def test_hidden_visual_flag():
    report = _single_visual_page(visual_container(display_hidden=True))
    assert report.pages[0].visuals[0].is_hidden


def test_page_and_report_level_filters():
    doc = layout(
        sections=[section(filters=[categorical_filter("pf", column_expr(entity_ref("Dates"), "Year"))])],
        report_filters=[categorical_filter("rf", column_expr(entity_ref("Geo"), "Country"))],
    )
    report = parse_layout(doc)
    assert [(r.table, r.name, r.scope) for r in report.filters] == [("Geo", "Country", "report:filter")]
    assert [(r.table, r.name, r.scope) for r in report.pages[0].filters] == [("Dates", "Year", "page:filter")]


def test_bookmark_pinned_filter_reference():
    # §5.3: a bookmark can pin a filter state referencing an otherwise
    # invisible field
    config = {
        "bookmarks": [
            {
                "name": "bm1",
                "explorationState": {
                    "filters": {"byExpr": [{"expression": column_expr(entity_ref("Sales"), "Channel")}]}
                },
            }
        ]
    }
    doc = layout(sections=[section()], config=config)
    report = parse_layout(doc)
    bookmark_refs = [r for r in report.filters if r.scope == "report:bookmark"]
    assert [(r.table, r.name) for r in bookmark_refs] == [("Sales", "Channel")]


def test_custom_visual_deep_scan_fallback():
    # §5.3: third-party visuals hide SourceRef/Property pairs in
    # non-standard places — the generic deep scan must still find them,
    # including through nested JSON-in-JSON strings
    import json as _json

    container = visual_container(
        visual_type="myCustomVisual1234",
        extra_single_visual={
            "customState": {"blob": _json.dumps({"binding": column_expr(entity_ref("Sales"), "SKU")})}
        },
    )
    report = _single_visual_page(container)
    refs = report.pages[0].visuals[0].references
    assert [(r.table, r.name, r.scope) for r in refs] == [("Sales", "SKU", "visual:other")]
    assert "!json" in refs[0].evidence


def test_hierarchy_level_reference():
    container = visual_container(
        prototype_query=_prototype(
            [
                {
                    "HierarchyLevel": {
                        "Expression": {
                            "Hierarchy": {"Expression": alias_ref("s"), "Hierarchy": "Geo Hierarchy"}
                        },
                        "Level": "Region",
                    },
                    "Name": "Sales.Geo Hierarchy.Region",
                }
            ]
        )
    )
    report = _single_visual_page(container)
    fields = report.pages[0].visuals[0].fields()
    assert ("Sales", "Geo Hierarchy.Region", RefKind.HIERARCHY_LEVEL) in fields
