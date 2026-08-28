"""Graph building, traversal, confidence propagation and the store."""

from __future__ import annotations

import pytest

from pbilineage.demo.fixtures import (
    DEMO_CAPACITY_SKUS,
    FINANCE_WORKSPACE,
    PIPELINE_DATASET,
    REVENUE_DATASET,
    SALES_WORKSPACE,
    build_demo_graph,
    demo_scan_result,
)
from pbilineage.graph.store import InMemoryGraphStore
from pbilineage.graph.traversal import impact_summary, traverse, weakest
from pbilineage.models import CapacityTier, Confidence, EdgeKind, NodeKind, node_id
from pbilineage.scan.normalize import (
    classify_partition_expression,
    dataflow_queries_from_model_json,
    snapshot_from_scan_results,
)


@pytest.fixture(scope="module")
def graph():
    return build_demo_graph()


@pytest.fixture(scope="module")
def store(graph):
    return InMemoryGraphStore(graph)


def find(graph, kind: NodeKind, name: str):
    matches = [n for n in graph.nodes.values() if n.kind is kind and n.name == name]
    assert matches, f"no {kind.value} named {name!r}"
    return matches[0]


# -- normalisation ----------------------------------------------------------
def test_scan_result_normalises_to_typed_workspaces():
    snapshot = snapshot_from_scan_results([demo_scan_result()], DEMO_CAPACITY_SKUS)
    assert [w.name for w in snapshot.workspaces] == ["Finance Analytics", "Sales Self-Service"]
    assert snapshot.workspaces[0].tier is CapacityTier.PREMIUM
    assert snapshot.workspaces[1].tier is CapacityTier.PRO


def test_pro_workspace_produces_a_confidence_warning():
    snapshot = snapshot_from_scan_results([demo_scan_result()], DEMO_CAPACITY_SKUS)
    assert any("shared capacity" in w for w in snapshot.warnings)


def test_calculated_columns_are_distinguished_from_imported_ones():
    snapshot = snapshot_from_scan_results([demo_scan_result()], DEMO_CAPACITY_SKUS)
    fact = snapshot.workspaces[0].datasets[0].table("FactSales")
    assert fact.column("MarginBand").is_calculated is True
    assert fact.column("Amount").is_calculated is False


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("let Source = 1 in Source", "m"),
        ("  \n  let\n Source = 1 in Source", "m"),
        ('Sql.Database("s","d")', "m"),
        ("SUMMARIZE(Sales, Sales[Region])", "calculated"),
        ("", "unknown"),
    ],
)
def test_partition_expressions_are_classified(expression, expected):
    assert classify_partition_expression(expression) == expected


def test_dataflow_model_json_splits_shared_queries():
    model_json = {
        "entities": [{"name": "Customer"}, {"name": "Product"}],
        "pbi:mashup": {
            "document": (
                "section Section1; shared Customer = let a = 1 in a; " "shared Product = let b = 2 in b;"
            )
        },
    }
    queries = dataflow_queries_from_model_json(model_json)
    assert queries["Customer"].startswith("let a")
    assert queries["Product"].startswith("let b")


# -- graph shape ------------------------------------------------------------
def test_every_layer_is_present(graph):
    kinds = {node.kind for node in graph.nodes.values()}
    assert {
        NodeKind.DATA_SOURCE,
        NodeKind.TABLE,
        NodeKind.COLUMN,
        NodeKind.MEASURE,
        NodeKind.REPORT,
        NodeKind.PAGE,
        NodeKind.VISUAL,
        NodeKind.DATAFLOW,
    } <= kinds


def test_node_ids_are_deterministic():
    first, second = build_demo_graph(), build_demo_graph()
    assert set(first.nodes) == set(second.nodes)


def test_rescanning_merges_instead_of_duplicating():
    store = InMemoryGraphStore(build_demo_graph())
    before = len(store.graph.nodes)
    store.write(build_demo_graph())
    assert len(store.graph.nodes) == before


def test_premium_workspace_dependencies_are_resolved(graph):
    total_revenue = node_id(NodeKind.MEASURE, REVENUE_DATASET, "FactSales", "Total Revenue")
    amount = node_id(NodeKind.COLUMN, REVENUE_DATASET, "FactSales", "Amount")
    edge = next(e for e in graph.edges if e.source == total_revenue and e.target == amount)
    assert edge.confidence is Confidence.RESOLVED
    assert edge.evidence == "DISCOVER_CALC_DEPENDENCY"


def test_pro_workspace_dependencies_are_heuristic(graph):
    open_pipeline = node_id(NodeKind.MEASURE, PIPELINE_DATASET, "Opportunity", "Open Pipeline")
    outgoing = [e for e in graph.edges if e.source == open_pipeline and e.kind is EdgeKind.DERIVES_FROM]
    assert outgoing
    assert all(e.confidence is Confidence.HEURISTIC for e in outgoing)
    assert all(e.evidence == "dax-tokenizer" for e in outgoing)


def test_m_rename_links_a_model_column_to_its_source_column(graph):
    amount = node_id(NodeKind.COLUMN, REVENUE_DATASET, "FactSales", "Amount")
    targets = [
        graph.nodes[e.target].name
        for e in graph.edges
        if e.source == amount and e.kind is EdgeKind.DERIVES_FROM
    ]
    assert "SalesAmount" in targets


def test_opaque_m_transform_downgrades_its_columns(graph):
    region = node_id(NodeKind.COLUMN, REVENUE_DATASET, "DimCustomer", "Region")
    outgoing = [e for e in graph.edges if e.source == region and e.kind is EdgeKind.DERIVES_FROM]
    assert outgoing
    assert all(e.confidence is Confidence.OPAQUE for e in outgoing)


def test_visual_bindings_are_resolved(graph):
    used_in = [e for e in graph.edges if e.kind is EdgeKind.USED_IN]
    assert used_in
    assert all(e.confidence is Confidence.RESOLVED for e in used_in)
    assert {e.properties.get("role") for e in used_in} >= {"Category", "Y"}


def test_conditional_formatting_is_its_own_role(graph):
    roles = {e.properties.get("role") for e in graph.edges if e.kind is EdgeKind.USED_IN}
    assert "conditional_formatting" in roles


def test_report_links_to_its_semantic_model(graph):
    report = find(graph, NodeKind.REPORT, "Revenue Overview")
    model = node_id(NodeKind.SEMANTIC_MODEL, REVENUE_DATASET)
    assert any(e.source == report.id and e.target == model for e in graph.edges)


def test_dataflow_entity_columns_reach_the_source(graph):
    entity = node_id(NodeKind.TABLE, f"dataflow:{'cccccccc-0000-0000-0000-000000000001'}", "CustomerMaster")
    assert entity in graph.nodes
    assert any(e.source == entity and e.kind is EdgeKind.DERIVES_FROM for e in graph.edges)


# -- traversal --------------------------------------------------------------
def test_upstream_from_a_measure_reaches_the_source_column(graph):
    total_revenue = node_id(NodeKind.MEASURE, REVENUE_DATASET, "FactSales", "Total Revenue")
    result = traverse(graph, total_revenue, direction="upstream", depth=6)
    names = {graph.nodes[n].name for n in result.node_ids}
    # Column-level lineage: the measure reaches its column and that column's
    # source column. The table itself is a container, not a lineage hop.
    assert {"Amount", "SalesAmount"} <= names


def test_downstream_from_a_source_column_reaches_a_visual(graph):
    source_column = next(
        n
        for n in graph.nodes.values()
        if n.kind is NodeKind.COLUMN and n.properties.get("is_source") and n.name == "SalesAmount"
    )
    result = traverse(graph, source_column.id, direction="downstream", depth=8)
    kinds = {graph.nodes[n].kind for n in result.node_ids}
    assert NodeKind.VISUAL in kinds


def test_path_confidence_is_the_weakest_link(graph):
    source_column = next(
        n
        for n in graph.nodes.values()
        if n.kind is NodeKind.COLUMN and n.properties.get("is_source") and n.name == "SalesAmount"
    )
    total_revenue = node_id(NodeKind.MEASURE, REVENUE_DATASET, "FactSales", "Total Revenue")
    result = traverse(graph, source_column.id, direction="downstream", depth=8)
    # The measure edge is resolved, but the M rename before it is heuristic.
    assert result.confidence[total_revenue] is Confidence.HEURISTIC


def test_min_confidence_prunes_weaker_edges(graph):
    region = node_id(NodeKind.COLUMN, REVENUE_DATASET, "DimCustomer", "Region")
    everything = traverse(graph, region, direction="upstream", depth=4)
    resolved_only = traverse(graph, region, direction="upstream", depth=4, min_confidence=Confidence.RESOLVED)
    assert len(resolved_only.node_ids) < len(everything.node_ids)


def test_traversal_depth_is_respected(graph):
    total_revenue = node_id(NodeKind.MEASURE, REVENUE_DATASET, "FactSales", "Total Revenue")
    shallow = traverse(graph, total_revenue, direction="upstream", depth=1)
    deep = traverse(graph, total_revenue, direction="upstream", depth=6)
    assert len(shallow.node_ids) < len(deep.node_ids)


def test_traversal_of_an_unknown_node_is_empty(graph):
    assert traverse(graph, "Column:does-not-exist").node_ids == set()


def test_weakest_confidence_helper():
    assert weakest(Confidence.RESOLVED, Confidence.OPAQUE) is Confidence.OPAQUE
    assert weakest(Confidence.RESOLVED, Confidence.RESOLVED) is Confidence.RESOLVED


def test_impact_summary_lists_affected_reports(graph):
    amount = node_id(NodeKind.COLUMN, REVENUE_DATASET, "FactSales", "Amount")
    summary = impact_summary(graph, amount, depth=8)
    assert summary["by_kind"].get("Visual", 0) >= 1
    assert summary["by_kind"].get("Measure", 0) >= 1
    assert all("confidence" in item for item in summary["affected"])


# -- store ------------------------------------------------------------------
def test_search_ranks_exact_matches_first(store):
    results = store.search("Total Revenue")
    assert results[0].name == "Total Revenue"


def test_search_can_filter_by_kind(store):
    results = store.search("", [NodeKind.REPORT], limit=10)
    assert {n.kind for n in results} == {NodeKind.REPORT}


def test_expand_returns_one_hop(store, graph):
    fact = node_id(NodeKind.TABLE, REVENUE_DATASET, "FactSales")
    subgraph = store.neighbours(fact)
    assert subgraph.root == fact
    assert all(fact in (e.source, e.target) for e in subgraph.edges)


def test_round_trips_through_json(tmp_path, graph):
    path = InMemoryGraphStore(graph).save(tmp_path / "graph.json")
    reloaded = InMemoryGraphStore.load(path)
    assert reloaded.stats() == graph.stats()


def test_loading_a_missing_graph_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="run 'pbilineage scan' first"):
        InMemoryGraphStore.load(tmp_path / "nope.json")


def test_replace_workspace_removes_stale_nodes(graph):
    store = InMemoryGraphStore(build_demo_graph())
    from pbilineage.models import LineageGraph

    store.replace_workspace(SALES_WORKSPACE, LineageGraph())
    remaining = {n.workspace_id for n in store.graph.nodes.values()}
    assert SALES_WORKSPACE not in remaining
    assert FINANCE_WORKSPACE in remaining


def test_replace_workspace_drops_dangling_edges():
    store = InMemoryGraphStore(build_demo_graph())
    from pbilineage.models import LineageGraph

    store.replace_workspace(SALES_WORKSPACE, LineageGraph())
    ids = set(store.graph.nodes)
    assert all(e.source in ids and e.target in ids for e in store.graph.edges)
