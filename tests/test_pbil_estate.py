"""Milestones 9–11: M expression index, rule engine, export/re-import,
and the headless end-to-end pipeline."""

import json
import sqlite3

import pytest
from click.testing import CliRunner

from pbi_lineage.cli import main
from pbi_lineage.mindex import MExpressionIndex, parse_m
from pbi_lineage.notebook import analyze_local_file, run_tenant_scan
from pbi_lineage.persist import ScanBundle, read_json, to_rows, write_json, write_sqlite
from pbi_lineage.resolve import STATUS_INCOMPLETE, STATUS_UNUSED, analyze_model
from pbi_lineage.rules import EstateContext, Rule, run_rules
from pbi_lineage.schema import (
    Column,
    FieldReference,
    Measure,
    Model,
    NamedExpression,
    Page,
    Partition,
    RefKind,
    Relationship,
    ReportLayout,
    Table,
    Visual,
)
from pbi_lineage.service.rest import Response, RestClient, StaticToken

from .pbil_fixtures import (
    alias_ref,
    layout,
    pbir_visual,
    column_expr,
    entity_ref,
    section,
    visual_container,
    write_pbip,
    write_pbix,
)

# ---------------------------------------------------------------------------
# M9: M expression index
# ---------------------------------------------------------------------------

SALES_M = '''let
    Source = Sql.Database("dwh.corp.local", "SalesDW"),
    dbo_FactSales = Source{[Schema="dbo",Item="FactSales"]}[Data],
    // legacy: used to read dbo.FactSales_Old
    #"Removed Columns" = Table.RemoveColumns(dbo_FactSales, {"Scratch"}),
    #"Filtered Rows" = Table.SelectRows(#"Removed Columns", each [Year] >= 2020)
in
    #"Filtered Rows"'''

GEO_M = """let
    Source = Sql.Database("dwh.corp.local", "SalesDW"),
    dim = Source{[Schema="dbo",Item="DimGeography"]}[Data]
in
    dim"""


def _m_model():
    return Model(
        name="SalesModel",
        tables=[
            Table(name="Sales", partitions=[Partition(name="p", source_type="m", expression=SALES_M)]),
            Table(name="Geo", partitions=[Partition(name="p", source_type="m", expression=GEO_M)]),
        ],
        expressions=[NamedExpression(name="Env", expression='"prod" meta [IsParameterQuery=true]')],
    )


def test_parse_m_extracts_steps_and_sources():
    steps, sources = parse_m(SALES_M)
    assert "Source" in steps and "Filtered Rows" in steps
    assert sources[0].function == "Sql.Database"
    assert sources[0].arguments == ["dwh.corp.local", "SalesDW"]
    assert (sources[0].schema, sources[0].item) == ("dbo", "FactSales")


def test_m_index_impact_of_table_is_anchor_confidence():
    index = MExpressionIndex()
    index.add_model(_m_model())
    rows = index.impact_of("FactSales")
    assert [row["entry_name"] for row in rows] == ["Sales"]
    assert rows[0]["confidence"] == "anchor"
    assert "Sql.Database" in rows[0]["evidence"]


def test_m_index_search_flags_comment_hits():
    index = MExpressionIndex()
    index.add_model(_m_model())
    hits = index.search("FactSales_Old")
    assert len(hits) == 1
    assert hits[0].in_comment  # a mention in a comment is not a real reference
    # and it must not appear as an impacted item
    assert index.impact_of("FactSales_Old") == []


def test_m_index_covers_dataflows_and_shared_expressions():
    index = MExpressionIndex()
    index.add_model(_m_model())
    index.add_dataflow(
        {
            "objectId": "df1",
            "name": "Staging",
            "entities": [{"name": "Cleaned", "mashupExpression": GEO_M}],
        },
        workspace_id="ws1",
    )
    rows = index.impact_of("DimGeography")
    names = {(row["item_name"], row["entry_name"]) for row in rows}
    assert ("SalesModel", "Geo") in names
    assert ("Staging", "Cleaned") in names
    assert any(entry.kind == "expression" for entry in index.entries)


# ---------------------------------------------------------------------------
# M10: rule engine
# ---------------------------------------------------------------------------


def _rules_model():
    return Model(
        name="RuleModel",
        tables=[
            Table(
                name="Sales",
                columns=[
                    Column(name="Qty", summarize_by="sum", data_type="int64"),
                    Column(name="Region", summarize_by="none"),
                    Column(name="OrderDate", data_type="dateTime"),
                    Column(name="MonthName", data_type="string"),
                ],
                measures=[Measure(name="Total", expression="SUM(Sales[Qty])")],
            ),
            Table(name="LocalDateTable_abc123", columns=[Column(name="Date", data_type="dateTime")]),
        ],
        relationships=[
            Relationship(
                name="R1",
                from_table="Sales",
                from_column="Region",
                to_table="Sales",
                to_column="Region",
                cross_filtering="bothDirections",
                to_cardinality="many",
            )
        ],
    )


def _report_with(refs):
    return ReportLayout(name="R", pages=[Page(name="p", visuals=[Visual(name="v", references=refs)])])


def test_rules_detect_implicit_measures_and_auto_date_and_relationships():
    model = _rules_model()
    report = _report_with(
        [
            FieldReference(
                table="Sales",
                name="Qty",
                kind=RefKind.COLUMN,
                scope="visual:projection",
                evidence="e",
            )
        ]
    )
    analysis = analyze_model(model, [report])
    findings = run_rules(EstateContext(model=model, analysis=analysis, reports=[report]))
    ids = {finding.rule_id for finding in findings}
    assert "implicit-measure" in ids
    assert "auto-date-table" in ids
    assert "bidirectional-relationship" in ids
    assert "many-to-many-relationship" in ids
    # errors sort first
    severities = [finding.severity for finding in findings]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])


def test_rules_detect_broken_visual_and_broken_dax():
    model = _rules_model()
    model.tables[0].measures.append(Measure(name="Bad", expression="SUM(Sales[DeletedColumn])"))
    report = _report_with(
        [
            FieldReference(
                table="Sales",
                name="GhostColumn",
                kind=RefKind.COLUMN,
                scope="visual:projection",
                evidence="e",
            )
        ]
    )
    analysis = analyze_model(model, [report])
    findings = run_rules(EstateContext(model=model, analysis=analysis, reports=[report]))
    ids = {finding.rule_id for finding in findings}
    assert "broken-visual-reference" in ids
    assert "broken-dax" in ids


def test_rules_high_cardinality_unused_and_duplicate_models():
    model = _rules_model()
    analysis = analyze_model(model, [_report_with([])])
    peer = _rules_model()
    peer.name = "RuleModel Copy"
    findings = run_rules(
        EstateContext(
            model=model,
            analysis=analysis,
            reports=[],
            peer_models=[peer],
            column_cardinality={("Sales", "MonthName"): 250_000},
        )
    )
    ids = {finding.rule_id for finding in findings}
    assert "high-cardinality-unused-column" in ids
    assert "duplicate-model" in ids


def test_rule_engine_is_data_driven_and_survives_a_bad_rule():
    def explode(context):
        raise RuntimeError("boom")

    findings = run_rules(
        EstateContext(model=_rules_model()),
        rules=[Rule("exploding", "warning", "bad rule", explode)],
    )
    assert len(findings) == 1
    assert "failed to evaluate" in findings[0].message


def test_disabled_rule_does_not_run():
    called = []

    def rule(context):
        called.append(1)
        return []

    run_rules(EstateContext(model=_rules_model()), rules=[Rule("r", "info", "d", rule, enabled=False)])
    assert not called


# ---------------------------------------------------------------------------
# M10: export / re-import
# ---------------------------------------------------------------------------


def _bundle():
    model = _rules_model()
    report = _report_with(
        [
            FieldReference(
                table="Sales",
                name="Qty",
                kind=RefKind.COLUMN,
                scope="visual:projection",
                evidence="page1/visual2/projection",
            )
        ]
    )
    analysis = analyze_model(model, [report])
    findings = run_rules(EstateContext(model=model, analysis=analysis, reports=[report]))
    return ScanBundle(
        model=model,
        analysis=analysis,
        reports=[report],
        findings=findings,
        workspace_id="ws1",
        model_id="ds1",
        coverage={"summary": "1 of 1 connected reports analyzed", "complete": True},
    )


def test_sqlite_export_has_section8_shape_and_evidence(tmp_path):
    path = write_sqlite(tmp_path / "scan.sqlite", [_bundle()], tenant_id="t1")
    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for expected in ("model", "table", "column_", "measure", "reference", "usage", "finding"):
            assert expected in tables
        # evidence_json is NOT NULL by schema and populated in practice
        evidences = [row[0] for row in connection.execute("SELECT evidence_json FROM reference")]
        assert evidences and all(json.loads(e)["evidence"] for e in evidences)
        statuses = {row[0] for row in connection.execute("SELECT status FROM usage")}
        assert statuses
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO reference(source_node_id,target_node_id,edge_kind,confidence,evidence_json)"
                " VALUES ('a','b','projects','parsed',NULL)"
            )
    finally:
        connection.close()


def test_json_export_round_trips(tmp_path):
    bundle = _bundle()
    path = write_json(tmp_path / "export.json", [bundle])
    scans = read_json(path)
    assert len(scans) == 1
    scan = scans[0]
    assert scan.model_name == "RuleModel"
    assert scan.coverage["complete"] is True
    # verdicts survive with their evidence, and lineage is queryable offline
    qty = scan.verdicts["column:Sales[Qty]"]
    assert qty["status"] == "Used"
    assert scan.consumers_of("column:Sales[Qty]")
    assert scan.by_status(STATUS_UNUSED)


def test_json_import_rejects_newer_schema(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"bundles": [{"schema_version": 99, "model": {"name": "x"}}]}))
    with pytest.raises(ValueError, match="newer than this tool supports"):
        read_json(path)


def test_row_export_shape_for_delta():
    rows = to_rows([_bundle()])
    assert {"model", "table", "column", "measure", "usage", "reference", "finding"} <= set(rows)
    assert rows["usage"] and "status" in rows["usage"][0]
    assert rows["reference"] and json.loads(rows["reference"][0]["evidence_json"])["evidence"]


# ---------------------------------------------------------------------------
# M11: headless pipeline + CLI
# ---------------------------------------------------------------------------


def _pbip_project(tmp_path):
    pages = {
        "p1": {
            "page": {"name": "p1"},
            "visuals": {"v1": pbir_visual(projections={"Values": [column_expr(entity_ref("Sales"), "Qty")]})},
        }
    }
    return write_pbip(tmp_path, pages=pages)


def test_analyze_local_file_end_to_end(tmp_path):
    output = analyze_local_file(_pbip_project(tmp_path), out_dir=tmp_path / "out")
    assert len(output.bundles) == 1
    bundle = output.bundles[0]
    assert bundle.analysis is not None
    # Qty is projected → Used; Margin (calc column, unreferenced) → Unused
    assert bundle.analysis.verdicts["column:Sales[Qty]"].status == "Used"
    assert output.json_path.exists() and output.sqlite_path.exists()
    assert "1 model(s) analyzed" in output.summary()


def test_analyze_thin_pbix_reports_missing_model(tmp_path):
    from .pbil_fixtures import THIN_CONNECTIONS

    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    path = write_pbix(
        tmp_path / "thin.pbix",
        layout(sections=[section(visual_containers=[container])]),
        include_datamodel=False,
        connections=THIN_CONNECTIONS,
    )
    output = analyze_local_file(path)
    assert output.bundles == []
    assert any("no model metadata" in w for w in output.warnings)


class _Transport:
    def __init__(self, scan_payload, export_bytes):
        self.scan_payload = scan_payload
        self.export_bytes = export_bytes

    def request(self, method, url, *, headers, json=None):
        if "admin/groups" in url:
            return Response(status=200, body={"value": []})
        if "workspaces/modified" in url:
            return Response(status=200, body=[{"id": "ws1"}])
        if "getInfo" in url:
            return Response(status=200, body={"id": "scan1"})
        if "scanStatus" in url:
            return Response(status=200, body={"status": "Succeeded"})
        if "scanResult" in url:
            return Response(status=200, body=self.scan_payload)
        if "/Export" in url:
            return Response(status=200, content=self.export_bytes)
        return Response(status=404, body={})


def test_run_tenant_scan_writes_exports_and_keeps_coverage(tmp_path):
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    export = write_pbix(
        tmp_path / "r1.pbix", layout(sections=[section(visual_containers=[container])])
    ).read_bytes()
    scan_payload = {
        "workspaces": [
            {
                "id": "ws1",
                "name": "Finance",
                "capacityId": "c1",
                "capacitySku": "P1",
                "isOnDedicatedCapacity": True,
                "datasets": [
                    {
                        "id": "ds1",
                        "name": "Sales Model",
                        "tables": [
                            {
                                "name": "Sales",
                                "columns": [{"name": "Qty"}, {"name": "Unused Col"}],
                                "measures": [{"name": "Total", "expression": "SUM(Sales[Qty])"}],
                            }
                        ],
                    }
                ],
                "reports": [{"id": "r1", "name": "Ops", "datasetId": "ds1"}],
            }
        ]
    }
    client = RestClient(_Transport(scan_payload, export), StaticToken("t"), sleep=lambda s: None)
    output = run_tenant_scan(client, out_dir=tmp_path / "run")
    assert len(output.bundles) == 1
    bundle = output.bundles[0]
    assert bundle.coverage["complete"] is True
    assert bundle.coverage["fidelity"] == "full"
    assert bundle.analysis.verdicts["column:Sales[Qty]"].status == "Used"
    assert bundle.analysis.verdicts["column:Sales[Unused Col]"].status == STATUS_UNUSED
    assert output.json_path.exists() and output.sqlite_path.exists()


def test_run_tenant_scan_export_failure_caps_verdicts(tmp_path):
    class FailingExport(_Transport):
        def request(self, method, url, *, headers, json=None):
            if "/Export" in url:
                return Response(status=403, body={"error": {"message": "sensitivity label"}})
            return super().request(method, url, headers=headers, json=json)

    scan_payload = {
        "workspaces": [
            {
                "id": "ws1",
                "name": "Finance",
                "datasets": [
                    {"id": "ds1", "name": "M", "tables": [{"name": "Sales", "columns": [{"name": "Qty"}]}]}
                ],
                "reports": [{"id": "r1", "name": "Ops", "datasetId": "ds1"}],
            }
        ]
    }
    client = RestClient(FailingExport(scan_payload, b""), StaticToken("t"), sleep=lambda s: None)
    output = run_tenant_scan(client, out_dir=tmp_path / "run")
    bundle = output.bundles[0]
    assert bundle.coverage["complete"] is False
    assert bundle.coverage["failures"][0]["cause"] == "sensitivity_label_encrypted"
    assert bundle.analysis.verdicts["column:Sales[Qty]"].status == STATUS_INCOMPLETE
    # a Pro workspace is also flagged degraded
    assert bundle.coverage["fidelity"] == "reduced"
    assert bundle.coverage["notices"]


def test_cli_analyze_and_removal_plan(tmp_path):
    project = _pbip_project(tmp_path)
    runner = CliRunner()

    result = runner.invoke(main, ["analyze", str(project), "--out", str(tmp_path / "cli")])
    assert result.exit_code == 0, result.output
    assert "Unused" in result.output

    blocked = runner.invoke(
        main, ["removal-plan", str(project), "--object", "Sales[Qty]", "--out", str(tmp_path / "m.json")]
    )
    assert blocked.exit_code == 0
    assert "BLOCKED" in blocked.output  # Qty is projected by a visual
    assert json.loads((tmp_path / "m.json").read_text())["blocked"] is True


def test_cli_search_m(tmp_path):
    project = write_pbip(
        tmp_path,
        pages={},
        tmdl_files={
            "tables/Sales.tmdl": (
                "table Sales\n"
                "\tcolumn Qty\n"
                "\t\tdataType: int64\n"
                "\tpartition p = m\n"
                "\t\tmode: import\n"
                "\t\tsource =\n"
                '\t\t\tlet Source = Sql.Database("srv", "dw"),\n'
                '\t\t\t    t = Source{[Schema="dbo",Item="FactSales"]}[Data]\n'
                "\t\t\tin t\n"
            )
        },
    )
    result = CliRunner().invoke(main, ["search-m", str(project), "FactSales"])
    assert result.exit_code == 0, result.output
    assert "Sales" in result.output and "anchor" in result.output
