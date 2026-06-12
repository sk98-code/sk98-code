"""Web app API tests (FastAPI TestClient — no live server needed)."""

import io
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bimigrate.web.app import create_app  # noqa: E402

from .conftest import QVS_SCRIPT, TWB_XML  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_index_and_info(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "bimigrate" in page.text and "Expression → DAX" in page.text
    info = client.get("/api/info").json()
    assert info["kb"]["conversion_rules"] >= 500
    assert ".twb" in info["extensions"]


def test_upload_discover_convert_roundtrip(client):
    files = [
        ("files", ("sales.twb", TWB_XML.encode(), "application/xml")),
        ("files", ("reload.qvs", QVS_SCRIPT.encode(), "text/plain")),
        ("files", ("notes.docx", b"nope", "application/octet-stream")),
    ]
    up = client.post("/api/upload", files=files).json()
    assert sorted(up["saved"]) == ["reload.qvs", "sales.twb"]
    assert up["rejected"] == ["notes.docx"]

    disc = client.post("/api/discover", params={"scrub": True}).json()
    names = {a["name"] for a in disc["artifacts"]}
    assert names == {"sales", "reload"}
    sales = next(a for a in disc["artifacts"] if a["name"] == "sales")
    assert sales["tool"] == "tableau" and sales["calculations"] == 3

    conv = client.post("/api/convert-estate").json()
    assert len(conv["projects"]) == 2
    assert any(d["tier"] == "ASSISTED" for d in conv["decisions"])

    bundle = client.get(conv["download"])
    assert bundle.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(bundle.content))
    assert any(n.endswith("sales.pbip") for n in archive.namelist())
    assert any(n.endswith("model.tmdl") for n in archive.namelist())

    assert client.post("/api/reset").json() == {"ok": True}
    assert client.post("/api/discover").status_code == 400


def test_expression_playground(client):
    res = client.post(
        "/api/convert-expression",
        json={"tool": "qlikview", "expression": "Sum({<Year={2024}>} Sales)", "table_hint": "Sales"},
    ).json()
    assert res["tier"] == "ASSISTED"
    assert "CALCULATE" in res["target_expression"]
    assert res["cited_rule"]["rule_id"] == res["rule_id"]
    assert (
        client.post("/api/convert-expression", json={"tool": "excel", "expression": "x"}).status_code == 400
    )


def test_script_and_ironpython_endpoints(client):
    script = client.post("/api/convert-script", json={"script": QVS_SCRIPT}).json()
    assert any(q["name"] == "Sales" for q in script["queries"])
    assert script["conversion_rate"] > 0.5

    triage = client.post(
        "/api/classify-script",
        json={"name": "x", "code": "table.ExportDataToFile(stream, writer)"},
    ).json()
    assert triage["tier"] == "MANUAL"
    assert triage["primary_intent"] == "export"


def test_mappings_and_benchmark_endpoints(client):
    rows = client.get("/api/mappings", params={"tool": "qlikview", "tier": "MANUAL"}).json()
    assert any(m["feature_name"] == "Macros (VBScript)" for m in rows)
    assert all(m["tier"] == "MANUAL" for m in rows)

    rules = client.get("/api/rules", params={"tool": "tableau", "query": "lod"}).json()
    assert any(r["rule_id"] == "tab-lod-fixed" for r in rules)

    bench = client.post("/api/benchmark").json()
    assert bench["passed"] == bench["total"] >= 40
