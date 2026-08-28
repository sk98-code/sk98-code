"""The FastAPI surface, over the in-memory store."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pbilineage.api.app import create_app
from pbilineage.demo.fixtures import REVENUE_DATASET, build_demo_graph
from pbilineage.graph.store import InMemoryGraphStore
from pbilineage.models import NodeKind, node_id

TOTAL_REVENUE = node_id(NodeKind.MEASURE, REVENUE_DATASET, "FactSales", "Total Revenue")
AMOUNT = node_id(NodeKind.COLUMN, REVENUE_DATASET, "FactSales", "Amount")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(InMemoryGraphStore(build_demo_graph())))


def test_health_reports_the_backend(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["nodes"] > 0


def test_stats_break_edges_down_by_confidence(client):
    payload = client.get("/api/stats").json()
    assert set(payload["lineage_edges_by_confidence"]) <= {"resolved", "heuristic", "opaque"}
    assert payload["nodes_by_kind"]["Measure"] > 0


def test_workspaces_carry_their_capacity_tier(client):
    payload = client.get("/api/workspaces").json()
    tiers = {w["name"]: w["properties"]["tier"] for w in payload}
    assert tiers["Finance Analytics"] == "premium"
    assert tiers["Sales Self-Service"] == "pro"


def test_search_finds_a_measure(client):
    payload = client.get("/api/search", params={"q": "Total Revenue"}).json()
    assert payload["count"] >= 1
    assert payload["results"][0]["name"] == "Total Revenue"


def test_search_filters_by_kind(client):
    payload = client.get("/api/search", params={"q": "", "kinds": "Report"}).json()
    assert {r["kind"] for r in payload["results"]} == {"Report"}


def test_search_rejects_an_unknown_kind(client):
    response = client.get("/api/search", params={"q": "x", "kinds": "Nonsense"})
    assert response.status_code == 400
    assert "unknown node kind" in response.json()["detail"]


def test_node_lookup_includes_neighbour_counts(client):
    payload = client.get(f"/api/nodes/{TOTAL_REVENUE}").json()
    assert payload["node"]["name"] == "Total Revenue"
    assert payload["upstream_count"] >= 1
    assert payload["downstream_count"] >= 1


def test_unknown_node_is_a_404(client):
    assert client.get("/api/nodes/Column:nope").status_code == 404


def test_expand_returns_one_hop(client):
    payload = client.get(f"/api/nodes/{AMOUNT}/expand").json()
    assert payload["root"] == AMOUNT
    assert payload["nodes"] and payload["edges"]
    assert all(AMOUNT in (edge["source"], edge["target"]) for edge in payload["edges"])


def test_expand_can_exclude_containment(client):
    payload = client.get(f"/api/nodes/{AMOUNT}/expand", params={"containment": "false"}).json()
    assert all(edge["kind"] != "contains" for edge in payload["edges"])


def test_lineage_upstream_and_downstream_differ(client):
    upstream = client.get(f"/api/lineage/{TOTAL_REVENUE}", params={"direction": "upstream"}).json()
    downstream = client.get(f"/api/lineage/{TOTAL_REVENUE}", params={"direction": "downstream"}).json()
    assert {n["id"] for n in upstream["nodes"]} != {n["id"] for n in downstream["nodes"]}


def test_lineage_reports_per_node_confidence(client):
    payload = client.get(f"/api/lineage/{TOTAL_REVENUE}", params={"depth": 5}).json()
    assert payload["meta"]["confidence"][TOTAL_REVENUE] == "resolved"


def test_lineage_min_confidence_prunes(client):
    everything = client.get(f"/api/lineage/{TOTAL_REVENUE}", params={"depth": 6}).json()
    strict = client.get(
        f"/api/lineage/{TOTAL_REVENUE}", params={"depth": 6, "min_confidence": "resolved"}
    ).json()
    assert len(strict["nodes"]) < len(everything["nodes"])


def test_lineage_rejects_a_bad_direction(client):
    assert client.get(f"/api/lineage/{TOTAL_REVENUE}", params={"direction": "sideways"}).status_code == 422


def test_lineage_rejects_a_bad_confidence(client):
    response = client.get(f"/api/lineage/{TOTAL_REVENUE}", params={"min_confidence": "certain"})
    assert response.status_code == 400


def test_impact_lists_downstream_visuals(client):
    payload = client.get(f"/api/impact/{AMOUNT}", params={"depth": 8}).json()
    assert payload["by_kind"].get("Visual", 0) >= 1
    assert payload["root"]["name"] == "Amount"


def test_warnings_are_exposed(client):
    payload = client.get("/api/warnings").json()
    assert payload["total"] >= 1
    assert any("shared capacity" in w for w in payload["warnings"])


def test_root_explains_how_to_build_the_ui_when_it_is_absent(tmp_path):
    # Explicit missing dist: the answer must not depend on whether the
    # developer running the tests happens to have built the UI.
    headless = TestClient(create_app(InMemoryGraphStore(), ui_dist=tmp_path / "absent"))
    payload = headless.get("/").json()
    assert "npm" in payload["build"]


def test_root_serves_the_bundle_once_it_is_built(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>lineage</title>")
    built = TestClient(create_app(InMemoryGraphStore(), ui_dist=dist))
    response = built.get("/")
    assert response.status_code == 200
    assert response.text.startswith("<!doctype html")
