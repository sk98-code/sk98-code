"""Dependency resolution: the DMV path, the parser fallback, and the routing."""

from __future__ import annotations

import pytest

from pbilineage.models import (
    CapacityTier,
    ColumnSpec,
    DatasetSpec,
    MeasureSpec,
    PartitionSpec,
    TableSpec,
    WorkspaceSpec,
)
from pbilineage.resolve.base import ObjectType
from pbilineage.resolve.dax_resolver import DaxDependencyResolver
from pbilineage.resolve.router import CapacityRouter, tier_from_sku, tier_from_workspace
from pbilineage.resolve.xmla_resolver import XmlaDependencyResolver


def build_dataset() -> DatasetSpec:
    sales = TableSpec(
        name="Sales",
        columns=[
            ColumnSpec(name="Amount", data_type="Double"),
            ColumnSpec(name="Cost", data_type="Double"),
            ColumnSpec(name="Region", data_type="String"),
            ColumnSpec(
                name="Margin",
                is_calculated=True,
                expression="Sales[Amount] - Sales[Cost]",
            ),
        ],
        measures=[
            MeasureSpec(name="Total Sales", table="Sales", expression="SUM(Sales[Amount])"),
            MeasureSpec(name="Total Cost", table="Sales", expression="SUM(Sales[Cost])"),
            MeasureSpec(name="Profit", table="Sales", expression="[Total Sales] - [Total Cost]"),
        ],
    )
    geography = TableSpec(
        name="Geography",
        columns=[ColumnSpec(name="Region", data_type="String")],
    )
    return DatasetSpec(id="ds1", name="Model", workspace_id="ws1", tables=[sales, geography])


def edges(result) -> set[tuple[str, str, str, str]]:
    return {(d.source.table, d.source.name, d.target.table, d.target.name) for d in result.dependencies}


# -- DAX parser fallback ----------------------------------------------------
def test_measure_resolves_to_its_column():
    result = DaxDependencyResolver().resolve(build_dataset())
    assert ("Sales", "Total Sales", "Sales", "Amount") in edges(result)


def test_measure_to_measure_reference_resolves():
    result = DaxDependencyResolver().resolve(build_dataset())
    assert ("Sales", "Profit", "Sales", "Total Sales") in edges(result)
    assert ("Sales", "Profit", "Sales", "Total Cost") in edges(result)


def test_calculated_column_dependencies_are_found():
    result = DaxDependencyResolver().resolve(build_dataset())
    assert ("Sales", "Margin", "Sales", "Amount") in edges(result)


def test_everything_from_the_parser_is_heuristic_at_best():
    result = DaxDependencyResolver().resolve(build_dataset())
    assert result.path == "dax-parser"
    assert all(d.confidence.value in ("heuristic", "opaque") for d in result.dependencies)


def test_bare_reference_prefers_a_column_on_the_home_table():
    dataset = build_dataset()
    dataset.tables[0].measures.append(
        MeasureSpec(name="Regions", table="Sales", expression="DISTINCTCOUNT([Region])")
    )
    result = DaxDependencyResolver().resolve(dataset)
    # 'Region' is on both Sales and Geography, but the measure lives on Sales,
    # which is how DAX itself resolves it.
    assert ("Sales", "Regions", "Sales", "Region") in edges(result)


def test_ambiguous_bare_reference_is_flagged_not_guessed():
    dataset = build_dataset()
    # 'Region' is on Sales and Geography but not on Calendar, so a bare
    # [Region] in a Calendar measure has no single right answer.
    dataset.tables.append(TableSpec(name="Calendar", columns=[ColumnSpec(name="Date", data_type="DateTime")]))
    dataset.tables[-1].measures.append(
        MeasureSpec(name="Regions", table="Calendar", expression="DISTINCTCOUNT([Region])")
    )
    result = DaxDependencyResolver().resolve(dataset)
    ambiguous = [d for d in result.dependencies if d.source.name == "Regions"]
    assert ambiguous and ambiguous[0].confidence.value == "opaque"
    assert "ambiguous" in ambiguous[0].note


def test_reference_to_a_missing_table_is_opaque_not_dropped():
    dataset = build_dataset()
    dataset.tables[0].measures.append(
        MeasureSpec(name="Ghost", table="Sales", expression="SUM(NotHere[Amount])")
    )
    result = DaxDependencyResolver().resolve(dataset)
    ghost = [d for d in result.dependencies if d.source.name == "Ghost"]
    assert ghost and ghost[0].confidence.value == "opaque"


def test_self_reference_is_not_emitted():
    dataset = build_dataset()
    dataset.tables[0].measures.append(MeasureSpec(name="Loop", table="Sales", expression="[Loop] + 1"))
    result = DaxDependencyResolver().resolve(dataset)
    assert not [d for d in result.dependencies if d.source.name == d.target.name == "Loop"]


def test_calculated_table_expression_is_resolved():
    dataset = build_dataset()
    dataset.tables.append(
        TableSpec(
            name="TopRegions",
            is_calculated=True,
            partitions=[
                PartitionSpec(
                    name="TopRegions",
                    source_type="calculated",
                    expression="SUMMARIZE(Sales, Sales[Region])",
                )
            ],
        )
    )
    result = DaxDependencyResolver().resolve(dataset)
    calc = [d for d in result.dependencies if d.source.object_type is ObjectType.CALC_TABLE]
    assert ("TopRegions", "", "Sales", "Region") in edges(result)
    assert calc


# -- XMLA / DMV path --------------------------------------------------------
class FakeRunner:
    def __init__(self, rows, fail: bool = False):
        self.rows = rows
        self.fail = fail
        self.statements: list[str] = []

    def query(self, dataset, statement):
        self.statements.append(statement)
        if self.fail:
            raise RuntimeError("no XMLA endpoint for this workspace")
        return self.rows


DMV_ROWS = [
    {
        "OBJECT_TYPE": "MEASURE",
        "TABLE": "Sales",
        "OBJECT": "Total Sales",
        "REFERENCED_OBJECT_TYPE": "COLUMN",
        "REFERENCED_TABLE": "Sales",
        "REFERENCED_OBJECT": "Amount",
        "EXPRESSION": "SUM(Sales[Amount])",
    },
    {
        "OBJECT_TYPE": "CALC_COLUMN",
        "TABLE": "Sales",
        "OBJECT": "Margin",
        "REFERENCED_OBJECT_TYPE": "COLUMN",
        "REFERENCED_TABLE": "Sales",
        "REFERENCED_OBJECT": "Cost",
    },
]


def test_dmv_rows_become_resolved_dependencies():
    result = XmlaDependencyResolver(FakeRunner(DMV_ROWS)).resolve(build_dataset())
    assert result.available is True
    assert result.path == "xmla-dmv"
    assert all(d.confidence.value == "resolved" for d in result.dependencies)
    assert ("Sales", "Total Sales", "Sales", "Amount") in edges(result)


def test_dmv_column_names_are_case_insensitive():
    rows = [{k.lower(): v for k, v in DMV_ROWS[0].items()}]
    result = XmlaDependencyResolver(FakeRunner(rows)).resolve(build_dataset())
    assert len(result.dependencies) == 1


def test_incomplete_dmv_rows_are_skipped():
    rows = [{"OBJECT_TYPE": "MEASURE", "TABLE": "Sales", "OBJECT": "Orphan"}]
    result = XmlaDependencyResolver(FakeRunner(rows)).resolve(build_dataset())
    assert result.dependencies == []


def test_unreachable_endpoint_reports_unavailable_rather_than_raising():
    result = XmlaDependencyResolver(FakeRunner([], fail=True)).resolve(build_dataset())
    assert result.available is False
    assert result.warnings


# -- routing ----------------------------------------------------------------
@pytest.mark.parametrize(
    "sku,expected",
    [
        ("P1", CapacityTier.PREMIUM),
        ("P3", CapacityTier.PREMIUM),
        ("F64", CapacityTier.FABRIC),
        ("F2", CapacityTier.FABRIC),
        ("A4", CapacityTier.PREMIUM),
        ("PP3", CapacityTier.PPU),
        ("EM2", CapacityTier.PRO),
        ("", CapacityTier.UNKNOWN),
    ],
)
def test_sku_maps_to_capacity_tier(sku, expected):
    assert tier_from_sku(sku) is expected


def test_workspace_without_capacity_is_pro():
    workspace = WorkspaceSpec(id="ws1", name="Shared")
    assert tier_from_workspace(workspace) is CapacityTier.PRO


def test_capacity_id_without_a_known_sku_still_tries_xmla():
    workspace = WorkspaceSpec(id="ws1", name="On capacity", capacity_id="cap-1")
    assert tier_from_workspace(workspace).has_xmla is True


def test_premium_workspace_routes_to_the_dmv_path():
    workspace = WorkspaceSpec(id="ws1", name="Premium", capacity_sku="P1", tier=CapacityTier.PREMIUM)
    router = CapacityRouter(xmla_resolver=XmlaDependencyResolver(FakeRunner(DMV_ROWS)))
    result = router.resolve(workspace, build_dataset())
    assert result.path == "xmla-dmv"
    assert router.summary() == {"xmla-dmv": 1}


def test_pro_workspace_routes_to_the_parser_and_says_why():
    workspace = WorkspaceSpec(id="ws1", name="Shared", tier=CapacityTier.PRO)
    router = CapacityRouter(xmla_resolver=XmlaDependencyResolver(FakeRunner(DMV_ROWS)))
    result = router.resolve(workspace, build_dataset())
    assert result.path == "dax-parser"
    assert "no XMLA endpoint" in result.warnings[0]


def test_premium_workspace_falls_back_when_xmla_is_unreachable():
    workspace = WorkspaceSpec(id="ws1", name="Premium", capacity_sku="P1", tier=CapacityTier.PREMIUM)
    router = CapacityRouter(xmla_resolver=XmlaDependencyResolver(FakeRunner([], fail=True)))
    result = router.resolve(workspace, build_dataset())
    assert result.path == "dax-parser"
    assert result.dependencies, "fallback must still produce lineage"
    assert "fell back to the DAX parser" in result.warnings[0]
    assert router.summary() == {"dax-parser+fallback": 1}


def test_resolution_path_is_recorded_on_the_dataset():
    workspace = WorkspaceSpec(id="ws1", name="Shared", tier=CapacityTier.PRO)
    dataset = build_dataset()
    CapacityRouter().resolve(workspace, dataset)
    assert dataset.resolution_path == "dax-parser"
