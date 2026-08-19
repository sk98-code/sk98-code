"""Local web UI: API behaviour over a real analysis of a generated project."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from pbi_lineage.web.app import create_app  # noqa: E402

from .pbil_fixtures import column_expr, entity_ref, measure_expr, pbir_visual, write_pbip  # noqa: E402

SALES_TMDL = """table Sales
\tcolumn Qty
\t\tdataType: int64
\t\tsummarizeBy: sum
\t\tsourceColumn: Qty

\tcolumn Region
\t\tdataType: string
\t\tsourceColumn: Region

\tcolumn DiscountCode
\t\tdataType: string
\t\tsourceColumn: DiscountCode

\tmeasure 'Total Sales' = SUM(Sales[Qty])
\t\tformatString: #,0

\tpartition Sales-p = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = Sql.Database("dwh", "SalesDW"),
\t\t\t\tt = Source{[Schema="dbo",Item="FactSales"]}[Data]
\t\t\tin
\t\t\t\tt
"""


@pytest.fixture()
def client(tmp_path):
    pages = {
        "p1": {
            "page": {"name": "p1", "displayName": "Overview"},
            "visuals": {
                "v1": pbir_visual(
                    name="v1",
                    projections={
                        "Values": [measure_expr(entity_ref("Sales"), "Total Sales")],
                        "Category": [column_expr(entity_ref("Sales"), "Region")],
                    },
                )
            },
        }
    }
    project = write_pbip(tmp_path, name="Demo", pages=pages, tmdl_files={"tables/Sales.tmdl": SALES_TMDL})
    c = TestClient(create_app())
    c.project = str(project)
    return c


def test_index_serves_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "pbi-lineage" in response.text


def test_state_empty_before_analysis(client):
    body = client.get("/api/state").json()
    assert body["loaded"] is False
    assert body["summary"] is None


def test_analyze_produces_log_and_summary(client):
    body = client.post("/api/analyze", json={"path": client.project}).json()
    assert body["ok"] is True
    messages = " ".join(row["message"] for row in body["log"])
    assert "Started execution" in messages
    assert "Building dependencies" in messages
    summary = body["summary"]
    assert summary["model"] == "Demo"
    assert summary["columns"]["total"] == 3
    assert summary["columns"]["unused"] == 1  # DiscountCode
    assert summary["by_status"]["Used"] >= 2
    assert summary["duration_s"] is not None


def test_analyze_rejects_bad_path(client):
    assert client.post("/api/analyze", json={"path": "/no/such/file.pbix"}).status_code == 400


def test_objects_filtering(client):
    client.post("/api/analyze", json={"path": client.project})
    everything = client.get("/api/objects").json()
    assert everything["total"] > 0

    unused = client.get("/api/objects", params={"status": "Unused", "kind": "column"}).json()
    assert [r["name"] for r in unused["rows"]] == ["DiscountCode"]
    assert unused["rows"][0]["deletable"] is True

    searched = client.get("/api/objects", params={"q": "region"}).json()
    assert any(r["name"] == "Region" for r in searched["rows"])

    used = client.get("/api/objects", params={"status": "Used", "kind": "column"}).json()
    qty = next(r for r in used["rows"] if r["name"] == "Qty")
    assert qty["reasons"] or qty["evidence"]  # a verdict always carries its justification


def test_tree_both_directions(client):
    client.post("/api/analyze", json={"path": client.project})
    up = client.get("/api/tree", params={"node": "column:Sales[Qty]", "direction": "up"}).json()
    labels = _labels(up["tree"])
    assert "Total Sales" in labels  # the measure consumes the column

    down = client.get("/api/tree", params={"node": "measure:Total Sales", "direction": "down"}).json()
    assert "Sales[Qty]" in _labels(down["tree"])

    assert client.get("/api/tree", params={"node": "column:Nope[X]"}).status_code == 404


def test_lineage_graph_endpoint(client):
    client.post("/api/analyze", json={"path": client.project})
    body = client.get("/api/lineage/graph").json()
    kinds = {card["kind"] for card in body["nodes"]}
    assert {"semantic_model", "report"} <= kinds
    assert all("fields" in card and "lane" in card for card in body["nodes"])
    assert all(edge["evidence"] for edge in body["edges"])


def test_tenant_lineage_graph_endpoint(client, tmp_path):
    scan = Path("demo_estate/sample_tenant_scan.json")
    client.post("/api/tenant/load", json={"mode": "replay", "path": str(scan)})
    body = client.get("/api/tenant/lineage/graph").json()
    kinds = {card["kind"] for card in body["nodes"]}
    assert {"source", "dataflow", "semantic_model", "report"} <= kinds
    assert not [e for e in body["edges"] if e["kind"] == "inferred"]

    inferred = client.get("/api/tenant/lineage/graph", params={"infer": "true"}).json()
    assert [e for e in inferred["edges"] if e["kind"] == "inferred"]


def _labels(node):
    out = [node["label"]]
    for child in node["children"]:
        out.extend(_labels(child))
    return out


def test_m_expressions_and_code(client):
    client.post("/api/analyze", json={"path": client.project})
    body = client.get("/api/m-expressions").json()
    assert body["entries"]
    entry = body["entries"][0]
    assert "Sql.Database" in entry["code"]
    assert any("FactSales" in s for s in entry["sources"])

    filtered = client.get("/api/m-expressions", params={"q": "FactSales"}).json()
    assert filtered["entries"]

    impact = client.get("/api/m-impact", params={"object_name": "FactSales"}).json()
    assert impact["rows"] and impact["rows"][0]["confidence"] == "anchor"


def test_findings_endpoint(client):
    client.post("/api/analyze", json={"path": client.project})
    body = client.get("/api/findings").json()
    assert isinstance(body["rows"], list)


def test_removal_preview_blocks_used_object(client):
    client.post("/api/analyze", json={"path": client.project})
    blocked = client.post("/api/removal-plan", json={"objects": ["column:Sales[Region]"]}).json()
    assert blocked["blocked"] is True
    assert blocked["tmsl"] == ""
    assert any("Used" in reason for reason in blocked["block_reasons"])


def test_removal_preview_scripts_unused_object(client):
    client.post("/api/analyze", json={"path": client.project})
    ok = client.post("/api/removal-plan", json={"objects": ["column:Sales[DiscountCode]"]}).json()
    assert ok["blocked"] is False
    assert "DiscountCode" in ok["tmsl"]
    assert ok["manifest"]["blocked"] is False


def test_endpoints_require_analysis_first(client):
    for path in ("/api/objects", "/api/findings", "/api/m-expressions"):
        assert client.get(path).status_code == 400


def test_export_json(client, tmp_path):
    client.post("/api/analyze", json={"path": client.project})
    response = client.get("/api/export", params={"fmt": "json"})
    assert response.status_code == 200
    assert b'"verdicts"' in response.content


# ---------------------------------------------------------------------------
# Service / tenant mode
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402

_SCAN = {
    "datasourceInstances": [
        {
            "datasourceId": "src1",
            "datasourceType": "Sql",
            "connectionDetails": {"server": "dwh", "database": "SalesDW"},
        }
    ],
    "workspaces": [
        {
            "id": "ws1",
            "name": "Finance",
            "capacitySku": "P1",
            "capacityId": "c1",
            "users": [{"emailAddress": "ada@x.com", "groupUserAccessRight": "Admin"}],
            "datasets": [
                {
                    "id": "ds1",
                    "name": "Sales Model",
                    "configuredBy": "ada@x.com",
                    "datasourceUsages": [{"datasourceInstanceId": "src1"}],
                    "roles": [
                        {
                            "name": "EMEA",
                            "members": [{"memberName": "bob@x.com"}],
                            "tablePermissions": [{"name": "Geo", "filterExpression": '[C]="DE"'}],
                        }
                    ],
                }
            ],
            "reports": [{"id": "r1", "name": "Ops", "datasetId": "ds1", "customVisuals": ["Sankey"]}],
        },
        {
            "id": "ws2",
            "name": "Marketing",
            "users": [],
            "reports": [{"id": "r2", "name": "Cross", "datasetId": "ds1", "datasetWorkspaceId": "ws1"}],
        },
    ],
}

_ACTIVITY = [
    {"Activity": "ViewReport", "ReportId": "r1", "UserId": "u1", "CreationTime": "2026-08-01T10:00:00Z"},
    {"Activity": "AnalyzedByExternalApplication", "DatasetId": "ds1", "UserId": "cfo@x.com"},
]


def _tenant_files(tmp_path):
    scan = tmp_path / "scan.json"
    scan.write_text(_json.dumps(_SCAN))
    activity = tmp_path / "activity.json"
    activity.write_text(_json.dumps(_ACTIVITY))
    return str(scan), str(activity)


def test_tenant_state_empty_before_load(client):
    body = client.get("/api/tenant/state").json()
    assert body["loaded"] is False


def test_tenant_replay_load(client, tmp_path):
    scan, activity = _tenant_files(tmp_path)
    body = client.post(
        "/api/tenant/load", json={"mode": "replay", "path": scan, "activity_path": activity}
    ).json()
    assert body["ok"] is True
    summary = body["summary"]
    assert summary["workspaces"] == 2
    assert summary["items"]["reports"] == 2
    # a workspace with no admin is surfaced, not hidden in a percentage
    assert "Marketing" in summary["orphaned_workspaces"]
    messages = " ".join(row["message"] for row in body["log"])
    assert "Replaying scan payload" in messages


def test_tenant_load_rejects_missing_and_bad_files(client, tmp_path):
    assert client.post("/api/tenant/load", json={"mode": "replay", "path": "/nope.json"}).status_code == 400
    junk = tmp_path / "junk.json"
    junk.write_text("not json")
    assert client.post("/api/tenant/load", json={"mode": "replay", "path": str(junk)}).status_code == 400


def test_live_mode_without_a_token_explains_how_to_get_one(client):
    response = client.post("/api/tenant/load", json={"mode": "live"})
    assert response.status_code == 400
    assert "token" in response.json()["detail"].lower()


def test_tenant_reports_require_a_loaded_scan(client):
    assert client.get("/api/tenant/usage").status_code == 400


def test_tenant_reports(client, tmp_path):
    scan, activity = _tenant_files(tmp_path)
    client.post("/api/tenant/load", json={"mode": "replay", "path": scan, "activity_path": activity})

    usage = client.get("/api/tenant/usage").json()["rows"]
    by_name = {r["report"]: r for r in usage}
    assert by_name["Ops"]["views"] == 1
    assert by_name["Cross"]["never_viewed"] is True

    security = client.get("/api/tenant/security").json()["rows"]
    assert security[0]["kind"] == "RLS" and security[0]["role"] == "EMEA"

    access = client.get("/api/tenant/access").json()["rows"]
    assert any(r["principal"] == "ada@x.com" and r["right"] == "Admin" for r in access)

    visuals = client.get("/api/tenant/custom-visuals").json()["rows"]
    assert visuals[0]["custom_visual"] == "Sankey"

    excel = client.get("/api/tenant/excel-users").json()["rows"]
    assert excel[0]["user"] == "cfo@x.com"

    assert client.get("/api/tenant/nonsense").status_code == 404


def test_tenant_lineage_views(client, tmp_path):
    scan, _ = _tenant_files(tmp_path)
    client.post("/api/tenant/load", json={"mode": "replay", "path": scan})

    sources = client.get("/api/tenant/lineage/items?view=sources").json()["rows"]
    assert sources[0]["source"] == "dwh.SalesDW"
    assert sources[0]["report_count"] == 2

    models = client.get("/api/tenant/lineage/items?view=models").json()["rows"]
    model = next(m for m in models if m["model"] == "Sales Model")
    # the Marketing report consumes a Finance model — the dependency that
    # breaks workspace reorganisations, so it is listed on its own
    assert [c["name"] for c in model["cross_workspace"]] == ["Cross"]
