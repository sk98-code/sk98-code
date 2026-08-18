"""End-to-end source → visual lineage."""

from pbi_lineage.lineage import attach_sources, end_to_end_rows, nid_source, sources_for_table
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.resolve import analyze_model
from pbi_lineage.schema import (
    Column,
    FieldReference,
    Measure,
    Model,
    Page,
    Partition,
    RefKind,
    ReportLayout,
    Table,
    Visual,
)

SQL_M = """let
    Source = Sql.Database("dwh.corp.local", "SalesDW"),
    dbo_FactSales = Source{[Schema="dbo",Item="FactSales"]}[Data]
in
    dbo_FactSales"""

FILE_M = """let
    Source = Excel.Workbook(File.Contents("C:\\\\data\\\\prices.xlsx"), null, true)
in
    Source"""


def _model():
    return Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                columns=[Column(name="Qty"), Column(name="Region"), Column(name="Dead")],
                measures=[Measure(name="Total", expression="SUM(Sales[Qty])")],
                partitions=[Partition(name="p", source_type="m", expression=SQL_M)],
            ),
            Table(
                name="Prices",
                columns=[Column(name="Price")],
                partitions=[Partition(name="p", source_type="m", expression=FILE_M)],
            ),
        ],
    )


def _report():
    return ReportLayout(
        name="Rep",
        pages=[
            Page(
                name="p1",
                display_name="Overview",
                visuals=[
                    Visual(
                        name="v1",
                        visual_type="barChart",
                        references=[
                            FieldReference(
                                table="Sales",
                                name="Total",
                                kind=RefKind.MEASURE,
                                scope="visual:projection",
                                evidence="e",
                            ),
                            FieldReference(
                                table="Sales",
                                name="Region",
                                kind=RefKind.COLUMN,
                                scope="visual:projection",
                                evidence="e",
                            ),
                        ],
                    )
                ],
            )
        ],
    )


def _index(model):
    index = MExpressionIndex()
    index.add_model(model)
    return index


def test_sql_source_is_schema_qualified():
    model = _model()
    refs = sources_for_table(_index(model), "Sales")
    assert len(refs) == 1
    assert refs[0].label == "dbo.FactSales"
    assert refs[0].system == "Sql.Database"
    assert "dwh.corp.local" in refs[0].display()


def test_file_source_shows_file_name_not_the_whole_call():
    model = _model()
    refs = sources_for_table(_index(model), "Prices")
    assert refs[0].label == "prices.xlsx"
    # the wrapping M call must not leak into the label or the context
    assert "File.Contents" not in refs[0].display()
    assert refs[0].display().count("prices.xlsx") == 1


def test_attach_sources_adds_nodes_and_edges():
    model = _model()
    analysis = analyze_model(model, [_report()])
    attach_sources(analysis.graph, model, _index(model))
    node_id = nid_source("dbo.FactSales (dwh.corp.local.SalesDW)")
    assert node_id in analysis.graph.nodes
    targets = {e.target for e in analysis.graph.out_edges("table:Sales")}
    assert node_id in targets


def test_end_to_end_rows_span_source_to_report():
    model = _model()
    analysis = analyze_model(model, [_report()])
    rows = end_to_end_rows(model, analysis, _index(model))
    used = [r for r in rows if r.column == "Region"]
    assert used, "Region is projected by a visual"
    row = used[0]
    assert row.source.startswith("dbo.FactSales")
    assert row.table == "Sales"
    assert row.status == "Used"
    assert row.visual_type == "barChart"
    assert row.page == "Overview"  # the page node carries its display name
    assert row.report == "Rep"


def test_column_reached_only_through_a_measure_lists_it():
    model = _model()
    analysis = analyze_model(model, [_report()])
    rows = end_to_end_rows(model, analysis, _index(model))
    qty = next(r for r in rows if r.column == "Qty")
    assert "Total" in qty.measures  # the measure is the reason Qty is used
    assert qty.visual_type == "barChart"


def test_unused_column_appears_with_no_visual():
    model = _model()
    analysis = analyze_model(model, [_report()])
    rows = end_to_end_rows(model, analysis, _index(model))
    dead = [r for r in rows if r.column == "Dead"]
    assert len(dead) == 1
    assert dead[0].status == "Unused"
    assert dead[0].visual is None and dead[0].page is None


def test_filters_narrow_the_walk():
    model = _model()
    analysis = analyze_model(model, [_report()])
    index = _index(model)
    only_prices = end_to_end_rows(model, analysis, index, table="Prices")
    assert {r.table for r in only_prices} == {"Prices"}
    one_column = end_to_end_rows(model, analysis, index, table="Sales", column="Qty")
    assert {r.column for r in one_column} == {"Qty"}
