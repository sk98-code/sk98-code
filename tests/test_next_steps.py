"""Tests for wave-2 features: 500+ rule set, IronPython classifier,
REST collectors (stubbed transports), benchmark harness."""

import json
from pathlib import Path

from click.testing import CliRunner

from bimigrate.cli import main
from bimigrate.collect import QlikQrsClient, TableauServerClient
from bimigrate.convert.ironpython import classify_ironpython
from bimigrate.kb import KnowledgeBase
from bimigrate.validate.benchmark import run_benchmark

# -- rule-set growth ---------------------------------------------------------


def test_rule_set_exceeds_500():
    kb = KnowledgeBase()
    counts = kb.init_from_seeds()
    assert counts["conversion_rules"] >= 500
    # spot-check wave-2 additions are queryable
    assert kb.lookup_function("qlikview", "monthname")["dax_template"].startswith("FORMAT")
    assert kb.lookup_function("spotfire", "q3")["dax_equivalent"] == "PERCENTILE.INC"
    assert kb.get_rule("qlik-sum-if").tier == "ASSISTED"
    assert kb.get_rule("tab-iif-zero-guard").tier == "ASSISTED"
    # new DAX reference entries validate
    assert kb.is_valid_target_function("dax", "COMBIN")
    assert kb.is_valid_target_function("dax", "ISEVEN")
    kb.close()


# -- IronPython classifier ---------------------------------------------------

EXPORT_SCRIPT = """
from Spotfire.Dxp.Application.Visuals import TablePlot
import smtplib
writer = Document.Data.CreateDataWriter(DataWriterTypeIdentifiers.ExcelXlsDataWriter)
table.ExportDataToFile(stream, writer)
server = smtplib.SMTP('mail.corp.local')
server.SendMail('from@x', 'to@x', msg)
"""

NAV_SCRIPT = """
for page in Document.Pages:
    if page.Title == 'Detail':
        Document.ActivePageReference = page
Document.Properties['SelectedRegion'] = 'EU'
"""


def test_classify_export_email_script():
    result = classify_ironpython("export-mail", EXPORT_SCRIPT)
    assert result.tier == "MANUAL"  # policy: scripts never auto-convert
    assert "export" in result.intents and "email" in result.intents
    assert result.primary_intent in ("export", "email")
    assert "Power Automate" in result.recommendation
    assert result.line_annotations  # per-line annotation present


def test_classify_navigation_property_script():
    result = classify_ironpython("nav", NAV_SCRIPT)
    assert "navigation" in result.intents
    assert "property_write" in result.intents
    # navigation (0.85) outranks property_write (0.65)
    assert result.primary_intent == "navigation"


def test_classify_unknown_script():
    result = classify_ironpython("mystery", "x = 1 + 1")
    assert result.primary_intent == "unknown"
    assert result.confidence == 0.0
    assert "manual review" in result.recommendation


# -- Tableau Server collector (stub transport) --------------------------------


def _tableau_stub(method, url, headers, body):
    if url.endswith("/auth/signin"):
        assert method == "POST"
        return 200, {}, json.dumps({"credentials": {"token": "tok-123", "site": {"id": "site-1"}}}).encode()
    assert headers.get("X-Tableau-Auth") == "tok-123", "authed call missing token"
    if "/workbooks/wb-1/content" in url:
        return 200, {"Content-Disposition": 'name="tableau_workbook"; filename="sales.twbx"'}, b"PK-fake"
    if "/workbooks" in url:
        return (
            200,
            {},
            json.dumps(
                {
                    "pagination": {"totalAvailable": "1"},
                    "workbooks": {"workbook": [{"id": "wb-1", "name": "Sales"}]},
                }
            ).encode(),
        )
    if "/schedules" in url:
        return (
            200,
            {},
            json.dumps(
                {
                    "schedules": {"schedule": [{"name": "Nightly", "frequency": "Daily"}]},
                }
            ).encode(),
        )
    if "/subscriptions" in url or "/dataAlerts" in url:
        return 200, {}, json.dumps({"pagination": {"totalAvailable": "0"}}).encode()
    if "/auth/signout" in url:
        return 204, {}, b""
    raise AssertionError(f"unexpected call {url}")


def test_tableau_collector(tmp_path: Path):
    client = TableauServerClient("https://tab.example.com", transport=_tableau_stub)
    client.sign_in("pat", "secret")
    items = client.collect_estate(tmp_path / "tableau")
    kinds = {i.kind for i in items}
    assert "workbook" in kinds and "schedule" in kinds
    wb = next(i for i in items if i.kind == "workbook")
    assert wb.local_path.name == "sales.twbx"
    assert wb.local_path.read_bytes() == b"PK-fake"
    assert (tmp_path / "tableau/server_metadata.json").exists()
    client.sign_out()


# -- Qlik QRS collector (stub transport) ----------------------------------------


def _qrs_stub(method, url, headers, body):
    assert "X-Qlik-Xrfkey" in headers and "X-Qlik-User" in headers
    if "/qrs/app/full" in url:
        return 200, {}, json.dumps([{"id": "app-1", "name": "Finance"}]).encode()
    if "/qrs/app/app-1/export/" in url:
        return 200, {}, json.dumps({"downloadPath": "/tempcontent/abc"}).encode()
    if "/tempcontent/abc" in url:
        return 200, {}, b"QVF-fake"
    if "/qrs/stream/full" in url:
        return 200, {}, json.dumps([{"id": "s1", "name": "Everyone"}]).encode()
    if "/qrs/reloadtask/full" in url:
        return 200, {}, json.dumps([{"id": "t1", "name": "Reload Finance"}]).encode()
    raise AssertionError(f"unexpected call {url}")


def test_qlik_qrs_collector(tmp_path: Path):
    client = QlikQrsClient("https://qlik.example.com", "CORP", "svc", transport=_qrs_stub)
    items = client.collect_estate(tmp_path / "qrs")
    kinds = [i.kind for i in items]
    assert kinds.count("app") == 1
    assert "stream" in kinds and "reload_task" in kinds
    app = next(i for i in items if i.kind == "app")
    assert app.local_path.read_bytes() == b"QVF-fake"
    assert (tmp_path / "qrs/qrs_metadata.json").exists()


# -- benchmark harness ------------------------------------------------------------


def test_benchmark_seed_corpus_passes():
    result = run_benchmark()
    assert result.total >= 40
    assert result.failures == [], result.failures
    matrix = result.report.accuracy_matrix()
    # accuracy is reported per tier x band, never blended
    tiers = {row["tier"] for row in matrix}
    assert {"AUTO", "ASSISTED", "MANUAL"} <= tiers


def test_benchmark_extra_corpus_and_failure_detection(tmp_path: Path):
    bad_case = [
        {
            "tool": "tableau",
            "expression": "SUM([Sales])",
            "expected_fragments": ["THIS_WILL_NOT_APPEAR("],
            "expected_tier": "AUTO",
            "band": "simple",
        }
    ]
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(bad_case))
    result = run_benchmark(extra_files=[extra])
    assert any(f["expected_fragments"] == ["THIS_WILL_NOT_APPEAR("] for f in result.failures)


def test_cli_benchmark(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(main, ["benchmark", "--out", str(tmp_path / "bench")])
    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "bench/benchmark_report.json").read_text())
    assert report["passed"] == report["total"]


def test_cli_collect_help():
    runner = CliRunner()
    assert runner.invoke(main, ["collect", "--help"]).exit_code == 0
    assert runner.invoke(main, ["collect", "tableau", "--help"]).exit_code == 0
    assert runner.invoke(main, ["collect", "qliksense", "--help"]).exit_code == 0
