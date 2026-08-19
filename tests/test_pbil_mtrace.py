"""Column-grain tracing through Power Query, and the lineage tree built on it.

The contract: follow what M actually says, and mark what cannot be followed
as untraced rather than guessing an origin.
"""

from pbi_lineage.lineage import column_lineage_graph, column_lineage_tree
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.mtrace import trace_table
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

RENAME_M = '''let
    Source = PostgreSQL.Database("localhost", "retail_demo"),
    nav = Source{[Schema="sales",Item="orders"]}[Data],
    #"Renamed" = Table.RenameColumns(nav,{{"total_amount","GrossAmount"}}),
    #"Added Risk" = Table.AddColumn(#"Renamed", "AdjustedRiskAmount", each [GrossAmount] * 1.15),
    #"Kept" = Table.SelectColumns(#"Added Risk", {"order_id","GrossAmount","AdjustedRiskAmount","status"})
in
    #"Kept"'''

NATIVE_M = """let
    Source = Sql.Database("dwh","SalesDW"),
    Q = Value.NativeQuery(Source, "SELECT o.order_id, o.total_amount AS GrossAmount FROM sales.orders o")
in
    Q"""

PIVOT_M = """let
    Source = Sql.Database("dwh","SalesDW"),
    nav = Source{[Schema="sales",Item="orders"]}[Data],
    p = Table.Pivot(nav, List.Distinct(nav[status]), "status", "total_amount")
in
    p"""


def test_rename_is_followed_to_the_source_column():
    trace = trace_table(RENAME_M, "Orders")
    assert trace.source_table == "sales.orders"
    gross = trace.origin("GrossAmount")
    assert gross.source_column == "total_amount"
    assert gross.derivation == "renamed"


def test_step_references_are_not_mistaken_for_columns():
    """`#"Renamed"` is a step reference; it must never become a column."""
    trace = trace_table(RENAME_M, "Orders")
    assert "Renamed" not in trace.columns
    assert "Kept" not in trace.columns
    assert set(trace.columns) == {"order_id", "GrossAmount", "AdjustedRiskAmount", "status"}


def test_computed_column_records_its_input_and_claims_no_source():
    origin = trace_table(RENAME_M, "Orders").origin("AdjustedRiskAmount")
    assert origin.derivation == "computed"
    assert origin.source_column is None  # it is not *from* a source column
    assert "GrossAmount" in origin.detail


def test_select_columns_drops_the_rest():
    trace = trace_table(RENAME_M, "Orders")
    assert "customer_id" not in trace.columns


def test_native_sql_aliases_are_resolved():
    trace = trace_table(NATIVE_M, "Orders")
    assert trace.source_table == "sales.orders"
    gross = trace.origin("GrossAmount")
    assert gross.source_column == "total_amount"
    assert gross.derivation == "native-sql"
    assert trace.origin("order_id").source_column == "order_id"


def test_select_star_is_not_guessed():
    trace = trace_table(
        'let S = Sql.Database("d","b"), Q = Value.NativeQuery(S, "SELECT * FROM t") in Q', "T"
    )
    assert trace.columns == {}  # nothing invented
    assert trace.unsupported_steps


def test_untraceable_steps_are_reported():
    trace = trace_table(PIVOT_M, "Orders")
    assert any("Table.Pivot" in s for s in trace.unsupported_steps)


def test_expand_after_join_names_its_columns():
    trace = trace_table(
        """let
    Source = Sql.Database("d","b"),
    nav = Source{[Schema="s",Item="orders"]}[Data],
    merged = Table.NestedJoin(nav, {"id"}, Lines, {"id"}, "L", JoinKind.LeftOuter),
    expanded = Table.ExpandTableColumn(merged, "L", {"line_amount","qty"})
in
    expanded""",
        "Orders",
    )
    assert "line_amount" in trace.columns and "qty" in trace.columns
    assert any("Table.NestedJoin" in s for s in trace.unsupported_steps)


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------


def _fixture():
    model = Model(
        name="PostgreSQL",
        tables=[
            Table(
                name="02_Orders",
                columns=[
                    Column(name="order_id"),
                    Column(name="GrossAmount"),
                    Column(name="AdjustedRiskAmount"),
                    Column(name="status"),
                ],
                measures=[Measure(name="Total Gross", expression="SUM('02_Orders'[GrossAmount])")],
                partitions=[Partition(name="p", source_type="m", expression=RENAME_M)],
            )
        ],
    )
    report = ReportLayout(
        name="Rep",
        pages=[
            Page(
                name="Page 1",
                visuals=[
                    Visual(
                        name="lineChart",
                        visual_type="lineChart",
                        references=[
                            FieldReference(
                                table="02_Orders",
                                name="GrossAmount",
                                kind=RefKind.COLUMN,
                                scope="visual:projection",
                                evidence="e",
                            )
                        ],
                    )
                ],
            )
        ],
    )
    index = MExpressionIndex()
    index.add_model(model)
    return model, report, index


def _find(nodes, name, kind=None):
    for node in nodes:
        if node["name"] == name and (kind is None or node["type"] == kind):
            return node
        found = _find(node["children"], name, kind)
        if found:
            return found
    return None


def test_tree_has_the_server_to_visual_hierarchy():
    model, report, index = _fixture()
    tree = column_lineage_tree(model, analyze_model(model, [report]), index)

    server = tree[0]
    assert server["name"] == "localhost" and server["type"] == "PostgreSQL Server"
    database = server["children"][0]
    assert database["name"] == "retail_demo" and database["source"] == "localhost (Server)"
    schema = database["children"][0]
    assert schema["name"] == "sales" and schema["type"] == "PostgreSQL Schema"
    table = schema["children"][0]
    assert table["name"] == "orders" and table["source"] == "sales (Schema)"

    source_column = _find(tree, "total_amount", "PostgreSQL column")
    assert source_column["source"] == "orders (Table)"
    semantic = source_column["children"][0]
    assert semantic["type"] == "Semantic Model"


def test_tree_carries_status_and_consumers():
    model, report, index = _fixture()
    tree = column_lineage_tree(model, analyze_model(model, [report]), index)

    gross = _find(tree, "GrossAmount", "Model column")
    assert gross["status"] == "Used"
    assert gross["source"] == "02_Orders (Table)"
    child_types = {c["type"] for c in gross["children"]}
    assert "Used in visual" in child_types
    assert "Model measure" in child_types


def test_computed_column_nests_under_the_column_it_derives_from():
    model, report, index = _fixture()
    tree = column_lineage_tree(model, analyze_model(model, [report]), index)
    gross = _find(tree, "GrossAmount", "Model column")
    names = {c["name"] for c in gross["children"]}
    assert "AdjustedRiskAmount" in names, "the derivation chain must nest"


def test_unused_source_column_is_still_shown():
    model, report, index = _fixture()
    tree = column_lineage_tree(model, analyze_model(model, [report]), index)
    order_id = _find(tree, "order_id", "Model column")
    assert order_id["status"] == "Unused"


# ---------------------------------------------------------------------------
# The same lineage, shaped for the node-graph canvas
# ---------------------------------------------------------------------------


def _graph():
    model, report, index = _fixture()
    return column_lineage_graph(model, analyze_model(model, [report]), index, [report])


def _card(graph, kind):
    return next(card for card in graph["nodes"] if card["kind"] == kind)


def _names(card):
    return {field["name"] for field in card["fields"]}


def test_graph_has_one_card_per_artifact_in_flow_order():
    graph = _graph()
    lanes = {card["kind"]: card["lane"] for card in graph["nodes"]}
    assert lanes["source"] < lanes["semantic_model"] < lanes["report"]
    assert _card(graph, "source")["name"] == "orders"
    assert _card(graph, "source")["badge"] == "PostgreSQL table"


def test_source_card_carries_the_traced_source_columns():
    graph = _graph()
    assert "total_amount" in _names(_card(graph, "source"))


def test_model_card_carries_columns_and_measures_with_status():
    graph = _graph()
    fields = {f["name"]: f for f in _card(graph, "semantic_model")["fields"]}
    assert fields["GrossAmount"]["status"] == "Used"
    assert fields["order_id"]["status"] == "Unused"
    assert fields["Total Gross"]["kind"] == "measure"


def test_report_card_holds_fields_not_pages_or_visuals():
    graph = _graph()
    names = _names(_card(graph, "report"))
    assert "GrossAmount" in names
    assert "Page 1" not in names, "a page is structure, not a consumed field"
    assert "lineChart" not in names, "a visual is structure, not a consumed field"


def test_the_hop_chain_runs_source_column_to_report_field():
    """The point of the canvas: one unbroken path per column."""
    graph = _graph()
    source = _card(graph, "source")["id"]
    model = _card(graph, "semantic_model")["id"]
    report = _card(graph, "report")["id"]
    pairs = {(e["source"], e["target"]) for e in graph["edges"]}

    assert (f"{source}::column::total_amount", f"{model}::column::GrossAmount") in pairs
    assert (f"{model}::column::GrossAmount", f"{report}::field::GrossAmount") in pairs


def test_every_hop_names_its_evidence():
    """§8: evidence is not optional — a line on the canvas is a claim."""
    graph = _graph()
    assert graph["edges"]
    assert all(edge["evidence"] for edge in graph["edges"])


def test_a_column_that_could_not_be_traced_is_counted_not_invented():
    """PIVOT_M loses its columns; the source card must say so rather than
    show a column it never saw."""
    model = Model(
        name="M",
        tables=[
            Table(
                name="Orders",
                columns=[Column(name="North"), Column(name="South")],
                partitions=[Partition(name="p", source_type="m", expression=PIVOT_M)],
            )
        ],
    )
    index = MExpressionIndex()
    index.add_model(model)
    graph = column_lineage_graph(model, analyze_model(model, []), index, [])
    source = _card(graph, "source")
    assert source["fields"] == []
    assert source["untraced"] == 2
    assert source["untraced_reason"]
