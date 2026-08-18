"""Local web UI: API behaviour over a real analysis of a generated project."""

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
    assert summary["columns"]["unused"] == 1          # DiscountCode
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
    assert qty["reasons"] or qty["evidence"]      # a verdict always carries its justification


def test_tree_both_directions(client):
    client.post("/api/analyze", json={"path": client.project})
    up = client.get("/api/tree", params={"node": "column:Sales[Qty]", "direction": "up"}).json()
    labels = _labels(up["tree"])
    assert "Total Sales" in labels          # the measure consumes the column

    down = client.get("/api/tree", params={"node": "measure:Total Sales", "direction": "down"}).json()
    assert "Sales[Qty]" in _labels(down["tree"])

    assert client.get("/api/tree", params={"node": "column:Nope[X]"}).status_code == 404


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
