"""Regressions found by running against a real Power BI dashboard.

Each test here encodes a bug that synthetic fixtures did not catch:

1. Visual `Where` clauses live in the container's executed `query`, not in
   `prototypeQuery` — a field filtered there and projected nowhere read as
   unused.
2. Analytics transforms (forecast, clustering) emit `TransformTableRef`
   pseudo-columns that are not model objects and must not be reported as
   broken references.
3. Protected objects (hierarchy levels, relationship endpoints, …) did not
   seed the reachability walk, so anything *they* depended on read as
   unused — e.g. a calculated column referenced only by another calculated
   column that a hierarchy keeps alive.
4. Extractors publish VertiPaq's internal `H$`/`U$`/`R$` structures
   alongside real tables; those are storage artifacts, not inventory.
"""

import json

from pbi_lineage.connectors.dmv import read_model_via_dmv
from pbi_lineage.graph import nid_column
from pbi_lineage.readers.layout_legacy import parse_layout
from pbi_lineage.readers.pbix import read_pbix
from pbi_lineage.resolve import STATUS_USED, analyze_model
from pbi_lineage.schema import (
    Column,
    Hierarchy,
    HierarchyLevel,
    Model,
    RefKind,
    Table,
)

from .pbil_fixtures import alias_ref, layout, section, visual_container, write_pbix


def _query_command(select, where=None):
    """The shape Power BI writes into visualContainer.query."""
    query = {
        "Version": 2,
        "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
        "Select": select,
    }
    if where is not None:
        query["Where"] = where
    return json.dumps({"Commands": [{"SemanticQueryDataShapeCommand": {"Query": query}}]})


def test_query_where_clause_is_extracted():
    """Bug 1: a column filtered only in the executed query must be found."""
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    container["query"] = _query_command(
        select=[{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        where=[
            {
                "Condition": {
                    "In": {
                        "Expressions": [{"Column": {"Expression": alias_ref("c"), "Property": "Year"}}],
                        "Values": [[{"Literal": {"Value": "'2021'"}}]],
                    }
                }
            }
        ],
    )
    report = parse_layout(layout(sections=[section(visual_containers=[container])]))
    refs = report.pages[0].visuals[0].references
    filtered = {(r.table, r.name) for r in refs if r.scope == "visual:filter"}
    assert ("Sales", "Year") in filtered, "query-level Where filter was not extracted"


def test_filter_only_column_is_not_reported_unused():
    """Bug 1, end to end: the whole point of extracting the Where clause."""
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    container["query"] = _query_command(
        select=[{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        where=[{"Condition": {"Column": {"Expression": alias_ref("c"), "Property": "Year"}}}],
    )
    report = parse_layout(layout(sections=[section(visual_containers=[container])]))
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty"), Column(name="Year")])])
    result = analyze_model(model, [report])
    assert result.verdicts[nid_column("Sales", "Year")].status == STATUS_USED


def test_transform_outputs_are_not_model_references():
    """Bug 2: forecast/cluster outputs must not become broken references."""
    container = visual_container(visual_type="lineChart")
    container["query"] = json.dumps(
        {
            "Commands": [
                {
                    "SemanticQueryDataShapeCommand": {
                        "Query": {
                            "Version": 2,
                            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
                            "Select": [
                                {
                                    "Column": {
                                        "Expression": {"TransformTableRef": {"Source": "output0"}},
                                        "Property": "forecastValue",
                                    }
                                },
                                {
                                    "Column": {
                                        "Expression": {"TransformTableRef": {"Source": "output0"}},
                                        "Property": "confidenceHighBound",
                                    }
                                },
                                {"Column": {"Expression": alias_ref("c"), "Property": "Qty"}},
                            ],
                        }
                    }
                }
            ]
        }
    )
    report = parse_layout(layout(sections=[section(visual_containers=[container])]))
    names = {r.name for r in report.pages[0].visuals[0].references}
    assert "Qty" in names
    assert "forecastValue" not in names
    assert "confidenceHighBound" not in names

    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty")])])
    result = analyze_model(model, [report])
    assert result.unresolved == [], "transform outputs leaked in as broken references"


def test_protected_object_seeds_reachability():
    """Bug 3: a hierarchy keeps Quarter alive; Quarter's DAX keeps QuarterNo
    alive, and QuarterNo's DAX keeps MonthNo alive."""
    model = Model(
        name="AutoDate",
        tables=[
            Table(
                name="DateTable",
                columns=[
                    Column(name="Date"),
                    Column(name="MonthNo", is_calculated=True, expression="MONTH([Date])"),
                    Column(name="QuarterNo", is_calculated=True, expression="INT(([MonthNo] + 2) / 3)"),
                    Column(name="Quarter", is_calculated=True, expression='"Qtr " & [QuarterNo]'),
                ],
                hierarchies=[
                    Hierarchy(
                        name="Date Hierarchy", levels=[HierarchyLevel(name="Quarter", column="Quarter")]
                    )
                ],
            )
        ],
    )
    result = analyze_model(model, [])
    assert result.verdicts[nid_column("DateTable", "Quarter")].status == STATUS_USED
    for downstream in ("QuarterNo", "MonthNo"):
        verdict = result.verdicts[nid_column("DateTable", downstream)]
        assert verdict.status == STATUS_USED, (
            f"{downstream} is consumed by a protected calculated column but read as " f"{verdict.status}"
        )


def test_internal_vertipaq_tables_are_not_inventory():
    """Bug 4: H$/U$/R$ rows are storage structures, not model tables."""

    class Executor:
        def query(self, dmv_query):
            if "TMSCHEMA_TABLES" in dmv_query:
                return [{"ID": 1, "Name": "Sales"}]
            if "TMSCHEMA_COLUMNS" in dmv_query:
                return [
                    {"ID": 10, "TableID": 1, "TableName": "Sales", "Name": "Qty", "Type": 1},
                    # real table missing from TMSCHEMA_TABLES — must be rebuilt
                    {"ID": 11, "TableID": 2, "TableName": "DateTable", "Name": "Year", "Type": 2},
                    # internal structures — must be dropped
                    {"ID": 12, "TableID": 3, "TableName": "H$Sales (12)$Qty (7)", "Name": "x", "Type": 1},
                    {"ID": 13, "TableID": 4, "TableName": "U$DateTable", "Name": "y", "Type": 1},
                    {"ID": 14, "TableID": 5, "TableName": "R$Sales (12)$abc", "Name": "z", "Type": 1},
                ]
            raise RuntimeError("not available")

    model = read_model_via_dmv(Executor(), name="M")
    assert sorted(t.name for t in model.tables) == ["DateTable", "Sales"]
    assert [c.name for c in model.table("DateTable").columns] == ["Year"]


def test_real_pbix_shape_end_to_end(tmp_path):
    """A PBIX whose only model reference sits in the query Where clause
    still analyzes cleanly through the public reader entry point."""
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    container["query"] = _query_command(
        select=[{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        where=[{"Condition": {"Column": {"Expression": alias_ref("c"), "Property": "Year"}}}],
    )
    path = write_pbix(
        tmp_path / "r.pbix",
        layout(sections=[section(visual_containers=[container])]),
        include_datamodel=False,
    )
    result = read_pbix(path)
    fields = {(r.table, r.name, r.kind) for r in result.report.all_references()}
    assert ("Sales", "Year", RefKind.COLUMN) in fields
