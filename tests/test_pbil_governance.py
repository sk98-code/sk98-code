"""Tenant governance analytics and cleanup actions."""

import json

from pbi_lineage import cleanup, governance
from pbi_lineage.resolve import analyze_model
from pbi_lineage.schema import (
    Column,
    FieldReference,
    Measure,
    Model,
    Page,
    RefKind,
    ReportLayout,
    Role,
    Table,
    Visual,
)


def _scan():
    return {
        "workspaces": [
            {
                "id": "ws1",
                "name": "Finance",
                "type": "Workspace",
                "capacitySku": "P1",
                "capacityId": "cap1",
                "users": [
                    {"emailAddress": "ada@x.com", "groupUserAccessRight": "Admin", "principalType": "User"},
                    {"emailAddress": "bob@x.com", "groupUserAccessRight": "Viewer", "principalType": "User"},
                ],
                "reports": [
                    {"id": "r1", "name": "Ops", "datasetId": "ds1", "customVisuals": ["Sankey", "Bullet"]},
                    {"id": "r2", "name": "Stale", "datasetId": "ds1", "customVisuals": ["Sankey"]},
                ],
                "datasets": [
                    {
                        "id": "ds1",
                        "name": "Sales Model",
                        "configuredBy": "ada@x.com",
                        "tables": [{"name": "Sales"}],
                        "roles": [
                            {
                                "name": "EMEA",
                                "members": [{"memberName": "bob@x.com"}],
                                "tablePermissions": [
                                    {"name": "Sales", "filterExpression": '[Region]="EMEA"'}
                                ],
                            }
                        ],
                    }
                ],
                "dataflows": [
                    {"objectId": "df1", "name": "Staging", "generation": 2, "entities": [{"name": "E"}]}
                ],
                "notebooks": [
                    {"id": "nb1", "name": "explore", "upstreamDatasets": [{"targetDatasetId": "ds1"}]}
                ],
                "apps": [
                    {
                        "id": "app1",
                        "name": "Finance App",
                        "publishedBy": "ada@x.com",
                        "audiences": [{"name": "Execs", "users": [{"emailAddress": "cto@x.com"}]}],
                    }
                ],
            },
            {"id": "ws2", "name": "Orphan", "type": "Workspace", "users": [], "reports": []},
        ]
    }


def _activity():
    return [
        {
            "Activity": "ViewReport",
            "ReportId": "r1",
            "UserId": "bob@x.com",
            "CreationTime": "2026-08-01T10:00:00Z",
        },
        {
            "Activity": "ViewReport",
            "ReportId": "r1",
            "UserId": "ada@x.com",
            "CreationTime": "2026-08-02T10:00:00Z",
        },
        {"Activity": "ViewReportPage", "ReportName": "Ops", "ReportPageName": "Overview"},
        {"Activity": "ViewReportPage", "ReportName": "Ops", "ReportPageName": "Overview"},
        {"Activity": "ViewReportPage", "ReportName": "Ops", "ReportPageName": "Detail"},
        {
            "Activity": "AnalyzedByExternalApplication",
            "DatasetId": "ds1",
            "DatasetName": "Sales Model",
            "UserId": "cfo@x.com",
            "CreationTime": "2026-08-03T09:00:00Z",
        },
    ]


# --------------------------------------------------------------- governance --


def test_tenant_summary_counts_and_finds_orphans():
    summary = governance.tenant_summary(_scan()).as_dict()
    assert summary["workspaces"] == 2
    assert summary["items"]["reports"] == 2
    assert summary["items"]["datasets"] == 1
    assert summary["by_capacity"]["P1"] == 1
    assert summary["users"] == 2
    # a workspace with no admin is a governance risk, not a silent row
    assert "Orphan" in summary["orphaned_workspaces"]


def test_access_matrix_covers_workspace_and_item_scope():
    rows = governance.access_matrix(_scan())
    scopes = {r["scope"] for r in rows}
    assert "workspace" in scopes
    principals = {r["principal"] for r in rows}
    assert {"ada@x.com", "bob@x.com"} <= principals
    assert any(r["right"] == "Admin" for r in rows)


def test_security_rules_extracts_rls():
    rows = governance.security_rules(_scan())
    assert rows and rows[0]["kind"] == "RLS"
    assert rows[0]["role"] == "EMEA"
    assert rows[0]["table"] == "Sales"
    assert "bob@x.com" in rows[0]["members"]


def test_inventories():
    scan = _scan()
    models = governance.model_inventory(
        scan,
        refreshables=[
            {
                "id": "ds1",
                "refreshCount": 30,
                "refreshFailures": 2,
                "lastRefresh": {"status": "Completed", "endTime": "2026-08-18T02:00:00Z"},
            }
        ],
    )
    assert models[0]["model"] == "Sales Model"
    assert models[0]["refresh_failures"] == 2
    assert models[0]["last_refresh_status"] == "Completed"

    assert governance.dataflow_inventory(scan)[0]["generation"] == "Gen2"
    assert governance.notebook_inventory(scan)[0]["notebook"] == "explore"
    apps = governance.apps_and_audiences(scan)
    assert apps[0]["audiences"] == ["Execs"] and apps[0]["users"] == 1


def test_report_usage_flags_never_viewed():
    rows = governance.report_usage(_scan(), _activity())
    by_name = {r["report"]: r for r in rows}
    assert by_name["Ops"]["views"] == 2
    assert by_name["Ops"]["distinct_viewers"] == 2
    assert by_name["Ops"]["last_viewed"].startswith("2026-08-02")
    assert by_name["Stale"]["never_viewed"] is True


def test_page_usage_and_excel_users():
    pages = governance.page_usage(_activity())
    assert pages[0]["page"] == "Overview" and pages[0]["views"] == 2
    excel = governance.excel_users(_activity())
    assert excel and excel[0]["user"] == "cfo@x.com"


def test_custom_visual_usage_for_licence_compliance():
    rows = governance.custom_visual_usage(_scan())
    top = rows[0]
    assert top["custom_visual"] == "Sankey"
    assert top["report_count"] == 2


def test_subscriptions_and_capacities():
    subs = governance.subscriptions(
        [
            {
                "title": "Weekly",
                "artifactDisplayName": "Ops",
                "artifactType": "Report",
                "ownerEmail": "ada@x.com",
                "users": [{"emailAddress": "bob@x.com"}],
                "frequency": "Weekly",
            }
        ]
    )
    assert subs[0]["recipients"] == ["bob@x.com"]
    caps = governance.capacity_metrics(
        [{"id": "cap1", "displayName": "P1 Cap", "sku": "P1", "state": "Active"}],
        refreshables=[{"capacity": {"id": "cap1"}, "refreshCount": 12}],
    )
    assert caps[0]["refreshes"] == 12


# ------------------------------------------------------------------ cleanup --


def _model():
    return Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                columns=[
                    Column(name="Qty", data_type="int64", summarize_by="sum"),
                    Column(name="Dead"),
                    Column(name="Calc", expression="Sales[Qty] * 2"),
                ],
                measures=[
                    Measure(name="Total", expression="SUM(Sales[Qty])", format_string="#,0"),
                    Measure(name="DeadM", expression="SUM(Sales[Dead])"),
                    Measure(name="Copy", expression="SUM( Sales[Qty] )  -- same as Total"),
                ],
            )
        ],
        relationships=[],
        roles=[Role(name="R", table_permissions={"Sales": "[Qty] > 0"})],
    )


def _report():
    return ReportLayout(
        name="R",
        pages=[
            Page(
                name="p",
                visuals=[
                    Visual(
                        name="v",
                        visual_type="card",
                        references=[
                            FieldReference(
                                table="Sales",
                                name="Total",
                                kind=RefKind.MEASURE,
                                scope="visual:projection",
                                evidence="e",
                            )
                        ],
                    )
                ],
            )
        ],
    )


def test_clean_tmdl_drops_only_unused():
    model = _model()
    analysis = analyze_model(model, [_report()])
    files = cleanup.clean_tmdl(model, analysis)
    table = files["definition/tables/Sales.tmdl"]
    assert "column Qty" in table  # used by Total
    assert "measure Total" in table
    assert "column Dead" not in table  # unused
    assert "measure DeadM" not in table
    # a role's DAX is preserved verbatim
    assert "[Qty] > 0" in files["definition/roles/R.tmdl"]


def test_clean_tmdl_can_keep_everything():
    model = _model()
    files = cleanup.clean_tmdl(model, analyze_model(model, [_report()]), drop_unused=False)
    assert "column Dead" in files["definition/tables/Sales.tmdl"]


def test_dax_backup_and_restore_round_trip():
    model = _model()
    backup = cleanup.backup_dax(model).as_dict()
    assert len(backup["measures"]) == 3
    assert len(backup["calculated_columns"]) == 1

    # someone breaks a measure, then restores
    model.tables[0].measures[0].expression = "BLANK()"
    changes = cleanup.restore_dax(model, backup)
    assert model.tables[0].measures[0].expression == "SUM(Sales[Qty])"
    assert any("reverted" in c for c in changes)

    # a deleted measure is put back
    model.tables[0].measures = [m for m in model.tables[0].measures if m.name != "Total"]
    changes = cleanup.restore_dax(model, backup)
    assert any(m.name == "Total" for m in model.tables[0].measures)
    assert any("restored missing" in c for c in changes)


def test_duplicate_dax_ignores_comments_and_whitespace():
    groups = cleanup.duplicate_dax(_model())
    assert groups, "Total and Copy are the same expression"
    names = {o["name"] for o in groups[0]["objects"]}
    assert names == {"Total", "Copy"}


def test_documentation_markdown_has_tables_and_status():
    model = _model()
    doc = cleanup.documentation_markdown(model, analyze_model(model, [_report()]), [_report()])
    assert "# M" in doc
    assert "## Tables" in doc
    assert "| Measure |" in doc
    assert "Unused" in doc


def test_save_and_resume_session(tmp_path):
    from pbi_lineage.persist import ScanBundle

    model = _model()
    bundle = ScanBundle(model=model, analysis=analyze_model(model, [_report()]), reports=[_report()])
    path = cleanup.save_session(
        tmp_path / "s.pbilineage.json",
        bundle,
        source_path="/tmp/x.pbix",
        log=[{"time": "1", "message": "ok"}],
    )
    loaded = cleanup.load_session(path)
    assert loaded["source_path"] == "/tmp/x.pbix"
    assert loaded["bundle"]["model"]["name"] == "M"
    assert loaded["log"][0]["message"] == "ok"


def test_load_session_rejects_foreign_file(tmp_path):
    import pytest

    path = tmp_path / "x.json"
    path.write_text(json.dumps({"kind": "something-else"}))
    with pytest.raises(ValueError, match="not a pbi-lineage session"):
        cleanup.load_session(path)
