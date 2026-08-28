"""DAX, M and report-layout parser behaviour."""

from __future__ import annotations

import json

import pytest

from pbilineage.models import Confidence
from pbilineage.parsers.dax import DaxRefKind, extract_dax_references, tokenize_dax
from pbilineage.parsers.layout import parse_layout
from pbilineage.parsers.m_query import StepKind, analyze_m_query, split_let_steps


def refs(expression: str) -> set[tuple[str, str, str]]:
    return {(r.kind.value, r.table.lower(), r.name.lower()) for r in extract_dax_references(expression)}


# -- DAX --------------------------------------------------------------------
def test_qualified_column_reference():
    assert ("column", "sales", "amount") in refs("SUM(Sales[Amount])")


def test_quoted_table_name_with_spaces():
    assert ("column", "sales table", "net amount") in refs("SUM('Sales Table'[Net Amount])")


def test_bare_bracket_is_ambiguous_until_resolved():
    assert refs("[Total Sales] * 2") == {("ambiguous", "", "total sales")}


def test_function_names_are_not_table_references():
    found = refs("CALCULATE(SUM(Sales[Amount]), ALL(Sales))")
    assert ("table", "", "sales") in found
    assert not any(name in ("calculate", "sum", "all") for _, _, name in found)


def test_string_literals_are_not_parsed_as_references():
    assert refs('IF(Sales[Region] = "Table[Column]", 1, 0)') == {("column", "sales", "region")}


def test_comments_are_ignored():
    expression = """
    // SUM(Ghost[Column])
    SUM(Sales[Amount]) /* Other[Thing] */ -- Third[Thing]
    """
    assert refs(expression) == {("column", "sales", "amount")}


def test_var_locals_are_not_model_objects():
    expression = "VAR Threshold = 100 RETURN CALCULATE([Total], Sales[Amount] > Threshold)"
    found = refs(expression)
    assert ("column", "sales", "amount") in found
    assert not any(name == "threshold" for _, _, name in found)


def test_escaped_bracket_inside_column_name():
    tokens = [t for t in tokenize_dax("Sales[Weird]]Name]") if t.is_significant]
    assert tokens[1].value == "Weird]Name"


def test_reference_deduplication_preserves_order():
    found = extract_dax_references("Sales[Amount] + Sales[Amount] + Sales[Cost]")
    assert [r.name for r in found if r.kind is DaxRefKind.COLUMN] == ["Amount", "Cost"]


# -- M ----------------------------------------------------------------------
SIMPLE_QUERY = """
let
    Source = Sql.Database("srv", "db"),
    Nav = Source{[Schema="dbo",Item="Orders"]}[Data],
    #"Removed Other Columns" = Table.SelectColumns(Nav, {"OrderId", "Amount", "Cost"}),
    #"Renamed Columns" = Table.RenameColumns(#"Removed Other Columns", {{"Amount", "Revenue"}}),
    #"Added Margin" = Table.AddColumn(#"Renamed Columns", "Margin", each [Revenue] - [Cost])
in
    #"Added Margin"
"""


def test_let_steps_are_split_on_top_level_commas():
    names = [name for name, _ in split_let_steps(SIMPLE_QUERY)]
    # `#"quoted step names"` are normalised to their bare text
    assert names == [
        "Source",
        "Nav",
        "Removed Other Columns",
        "Renamed Columns",
        "Added Margin",
    ]


def test_source_and_navigation_are_recognised():
    analysis = analyze_m_query(SIMPLE_QUERY)
    assert analysis.sources[0].kind == "Sql"
    assert analysis.sources[0].server == "srv"
    assert analysis.sources[0].database == "db"
    assert analysis.sources[0].item == "Orders"
    assert analysis.steps[1].kind is StepKind.NAVIGATION


def test_rename_traces_back_to_the_original_column():
    analysis = analyze_m_query(SIMPLE_QUERY)
    revenue = analysis.lineage_for("Revenue")
    assert revenue is not None
    assert revenue.source_columns == {"Amount"}
    assert revenue.confidence is Confidence.HEURISTIC


def test_added_column_traces_through_both_inputs():
    analysis = analyze_m_query(SIMPLE_QUERY)
    margin = analysis.lineage_for("Margin")
    assert margin is not None
    # Revenue was renamed from Amount, so Margin traces to Amount and Cost.
    assert margin.source_columns == {"Amount", "Cost"}


def test_select_columns_drops_everything_else():
    analysis = analyze_m_query(SIMPLE_QUERY)
    assert analysis.column_set_known is True
    assert "OrderId" in analysis.columns


def test_removed_column_disappears():
    query = """
    let
        Source = Sql.Database("s", "d"),
        Kept = Table.SelectColumns(Source, {"A", "B"}),
        Dropped = Table.RemoveColumns(Kept, {"B"})
    in
        Dropped
    """
    analysis = analyze_m_query(query)
    assert set(analysis.columns) == {"A"}


def test_unrecognised_transform_makes_the_query_opaque():
    query = """
    let
        Source = Sql.Database("s", "d"),
        Kept = Table.SelectColumns(Source, {"A"}),
        Weird = Table.FuzzyGroup(Kept, "A", {{"Cluster", each _}})
    in
        Weird
    """
    analysis = analyze_m_query(query)
    assert analysis.opaque is True
    assert "Table.FuzzyGroup" in analysis.unrecognized
    assert analysis.columns["A"].confidence is Confidence.OPAQUE


def test_passthrough_functions_do_not_taint_confidence():
    query = """
    let
        Source = Sql.Database("s", "d"),
        Kept = Table.SelectColumns(Source, {"A"}),
        Filtered = Table.SelectRows(Kept, each [A] > 0)
    in
        Filtered
    """
    analysis = analyze_m_query(query)
    assert analysis.opaque is False
    assert analysis.columns["A"].confidence is Confidence.HEURISTIC


def test_native_query_text_is_captured_for_later_sql_lineage():
    query = """
    let
        Source = Sql.Database("s", "d"),
        Q = Value.NativeQuery(Source, "SELECT a, b FROM dbo.T", null, [EnableFolding=true])
    in
        Q
    """
    analysis = analyze_m_query(query)
    native = [s for s in analysis.sources if s.native_query]
    assert native and native[0].native_query.startswith("SELECT a, b")


def test_expand_table_column_renames_expanded_fields():
    query = """
    let
        Source = Sql.Database("s", "d"),
        Expanded = Table.ExpandTableColumn(Source, "Customer", {"Name", "City"},
            {"Customer.Name", "Customer.City"})
    in
        Expanded
    """
    analysis = analyze_m_query(query)
    assert analysis.columns["Customer.Name"].source_columns == {"Customer.Name"}
    assert "Customer" not in analysis.columns


def test_group_by_keeps_keys_and_derives_aggregates():
    query = """
    let
        Source = Sql.Database("s", "d"),
        Grouped = Table.Group(Source, {"Region"}, {{"Total", each List.Sum([Amount]), type number}})
    in
        Grouped
    """
    analysis = analyze_m_query(query)
    assert set(analysis.columns) == {"Region", "Total"}
    assert analysis.columns["Total"].source_columns == {"Amount"}


def test_query_without_let_is_still_analysed():
    analysis = analyze_m_query('Sql.Database("srv", "db")')
    assert analysis.sources and analysis.sources[0].server == "srv"


def test_malformed_m_does_not_raise():
    analysis = analyze_m_query('let Source = Sql.Database("s"')
    assert isinstance(analysis.steps, list)


# -- report layout ----------------------------------------------------------
def build_layout() -> dict:
    visual = {
        "name": "v1",
        "singleVisual": {
            "visualType": "barChart",
            "projections": {
                "Category": [{"queryRef": "Dim.Region"}],
                "Y": [{"queryRef": "Fact.Total Sales"}],
            },
            "prototypeQuery": {
                "From": [
                    {"Name": "d", "Entity": "Dim", "Type": 0},
                    {"Name": "f", "Entity": "Fact", "Type": 0},
                ],
                "Select": [
                    {
                        "Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "Region"},
                        "Name": "Dim.Region",
                    },
                    {
                        "Measure": {
                            "Expression": {"SourceRef": {"Source": "f"}},
                            "Property": "Total Sales",
                        },
                        "Name": "Fact.Total Sales",
                    },
                ],
            },
            "objects": {
                "dataPoint": [
                    {
                        "properties": {
                            "fill": {
                                "solid": {
                                    "color": {
                                        "expr": {
                                            "Measure": {
                                                "Expression": {"SourceRef": {"Entity": "Fact"}},
                                                "Property": "Margin %",
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                ]
            },
        },
    }
    page_filter = [
        {
            "expression": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "Fact"}},
                    "Property": "OrderDate",
                }
            }
        }
    ]
    return {
        "sections": [
            {
                "name": "Section1",
                "displayName": "Overview",
                "ordinal": 0,
                "filters": json.dumps(page_filter),
                "visualContainers": [{"config": json.dumps(visual)}],
            }
        ]
    }


def test_layout_yields_pages_and_visuals():
    pages = parse_layout(build_layout())
    assert len(pages) == 1
    assert pages[0].name == "Overview"
    assert len(pages[0].visuals) == 1


def test_projection_roles_are_preserved():
    visual = parse_layout(build_layout())[0].visuals[0]
    roles = {(f.table, f.field): f.role for f in visual.fields}
    assert roles[("Dim", "Region")] == "Category"
    assert roles[("Fact", "Total Sales")] == "Y"


def test_alias_source_refs_resolve_to_entities():
    visual = parse_layout(build_layout())[0].visuals[0]
    assert all(f.table in ("Dim", "Fact") for f in visual.fields)


def test_conditional_formatting_measure_is_captured():
    visual = parse_layout(build_layout())[0].visuals[0]
    formatting = [f for f in visual.fields if f.field == "Margin %"]
    assert formatting and formatting[0].role == "conditional_formatting"


def test_page_filters_are_captured():
    page = parse_layout(build_layout())[0]
    assert [(f.table, f.field, f.role) for f in page.fields] == [("Fact", "OrderDate", "filter")]


def test_layout_accepts_a_json_string():
    assert parse_layout(json.dumps(build_layout()))[0].name == "Overview"


@pytest.mark.parametrize("payload", [None, "", "not json", {}, {"sections": "nope"}])
def test_malformed_layout_returns_no_pages(payload):
    assert parse_layout(payload) == []
