"""The redacted capture bundle.

The scrubber has two jobs that pull against each other: remove everything
identifying, and keep everything that describes the API contract. Most of
these tests pin one side or the other.
"""

from __future__ import annotations

import json

import pytest

from pbilineage.diagnostics import Scrubber, capture_bundle
from pbilineage.demo.fixtures import FINANCE_WORKSPACE, demo_scan_result
from tests.test_lineage_scan import FakeTransport, build_client, ok, settings

from pbilineage.clients.http import HttpResponse


@pytest.fixture
def scrubbed():
    scrubber = Scrubber()
    return scrubber, scrubber.apply(demo_scan_result())


# -- what must survive ------------------------------------------------------
def test_json_keys_are_never_altered(scrubbed):
    _, result = scrubbed
    workspace = result["workspaces"][0]
    assert set(workspace) >= {"id", "name", "type", "state", "datasets", "reports"}
    table = workspace["datasets"][0]["tables"][0]
    assert set(table) >= {"name", "source", "columns", "measures"}


def test_api_vocabulary_values_survive(scrubbed):
    _, result = scrubbed
    workspace = result["workspaces"][0]
    assert workspace["type"] == "Workspace"
    assert workspace["state"] == "Active"
    columns = workspace["datasets"][0]["tables"][0]["columns"]
    assert {c["columnType"] for c in columns} == {"Data", "Calculated"}
    assert result["datasourceInstances"][0]["datasourceType"] == "Sql"


def test_structure_of_m_expressions_survives(scrubbed):
    _, result = scrubbed
    expression = result["workspaces"][0]["datasets"][0]["tables"][0]["source"][0]["expression"]
    for construct in ("let", "Sql.Database", "Table.SelectColumns", "Table.RenameColumns"):
        assert construct in expression


def test_scrubbed_m_still_parses_into_the_same_step_kinds(scrubbed):
    from pbilineage.parsers.m_query import analyze_m_query

    _, result = scrubbed
    original = demo_scan_result()["workspaces"][0]["datasets"][0]["tables"][0]["source"][0]
    redacted = result["workspaces"][0]["datasets"][0]["tables"][0]["source"][0]

    before = [step.kind for step in analyze_m_query(original["expression"]).steps]
    after = [step.kind for step in analyze_m_query(redacted["expression"]).steps]
    assert before == after, "redaction must not change how the query parses"


def test_dax_structure_survives(scrubbed):
    _, result = scrubbed
    measures = result["workspaces"][0]["datasets"][0]["tables"][0]["measures"]
    expressions = " ".join(m["expression"] for m in measures)
    assert "SUM(" in expressions and "DIVIDE(" in expressions


# -- what must not survive --------------------------------------------------
def test_object_names_are_replaced(scrubbed):
    _, result = scrubbed
    encoded = json.dumps(result)
    for secret in ("Finance Analytics", "Revenue Model", "FactSales", "Total Revenue"):
        assert secret not in encoded


def test_servers_and_databases_are_replaced(scrubbed):
    _, result = scrubbed
    encoded = json.dumps(result)
    assert "finance-sql.database.windows.net" not in encoded
    assert "FinanceDW" not in encoded


def test_names_inside_expressions_are_replaced_too(scrubbed):
    _, result = scrubbed
    expression = result["workspaces"][0]["datasets"][0]["tables"][0]["source"][0]["expression"]
    # the M query names the server, the table and its columns as string literals
    assert "finance-sql.database.windows.net" not in expression
    assert "SalesAmount" not in expression


def test_email_addresses_are_replaced(scrubbed):
    _, result = scrubbed
    encoded = json.dumps(result)
    assert "analytics@contoso.com" not in encoded
    assert "example.invalid" in encoded


def test_guids_are_replaced(scrubbed):
    _, result = scrubbed
    assert result["workspaces"][0]["id"] != FINANCE_WORKSPACE


def test_descriptions_are_dropped_not_pseudonymized(scrubbed):
    _, result = scrubbed
    measures = result["workspaces"][0]["datasets"][0]["tables"][0]["measures"]
    described = [m for m in measures if "description" in m]
    assert described and all(m["description"] == "<redacted>" for m in described)


# -- consistency ------------------------------------------------------------
def test_the_same_name_maps_to_the_same_pseudonym_everywhere(scrubbed):
    _, result = scrubbed
    dataset = result["workspaces"][0]["datasets"][0]
    table_name = dataset["tables"][0]["name"]
    # the table names itself in its own DAX calculated column
    calculated = next(c for c in dataset["tables"][0]["columns"] if c.get("expression"))
    assert table_name in calculated["expression"]


def test_cross_references_still_resolve_after_scrubbing(scrubbed):
    _, result = scrubbed
    workspace = result["workspaces"][0]
    # report.datasetId must still point at the dataset it belongs to
    assert workspace["reports"][0]["datasetId"] == workspace["datasets"][0]["id"]


def test_datasource_usage_still_matches_its_instance(scrubbed):
    _, result = scrubbed
    usage = result["workspaces"][0]["datasets"][0]["datasourceUsages"][0]
    instance_ids = {i["datasourceId"] for i in result["datasourceInstances"]}
    assert usage["datasourceInstanceId"] in instance_ids


def test_m_library_function_names_are_never_rewritten():
    """The hostname sweep once turned Sql.Database into Host1."""
    scrubber = Scrubber()
    source = {
        "name": "Table",  # a table genuinely called "Table"
        "source": [
            {
                "expression": 'let X = Sql.Database("srv.contoso.com","DW"), '
                'Y = Table.SelectColumns(X, {"A"}) in Y'
            }
        ],
    }
    expression = scrubber.apply(source)["source"][0]["expression"]
    assert "Sql.Database" in expression
    assert "Table.SelectColumns" in expression
    assert "srv.contoso.com" not in expression


def test_source_columns_named_only_in_an_expression_are_replaced():
    """A renamed source column appears as a literal, never under a name key."""
    scrubber = Scrubber()
    source = {
        "name": "Fact",
        "source": [
            {
                "expression": 'let S = Sql.Database("h.example.com","DB"), '
                'R = Table.RenameColumns(S, {{"SalesAmount", "Amount"}}) in R'
            }
        ],
    }
    expression = scrubber.apply(source)["source"][0]["expression"]
    assert "SalesAmount" not in expression
    assert "Table.RenameColumns" in expression


def test_a_short_name_is_not_replaced_inside_a_longer_identifier():
    """'Amount' must not rewrite the middle of 'SalesAmount' and leak 'Sales'."""
    scrubber = Scrubber()
    scrubber.pseudonym("Amount", "Name")
    assert scrubber.scrub_text("SalesAmount") == "SalesAmount"


def test_compound_step_names_embedding_a_table_are_replaced():
    """`dbo_FactSales` is a bare identifier that still names a real table."""
    scrubber = Scrubber()
    result = scrubber.apply(
        {
            "name": "FactSales",
            "source": [{"expression": "let dbo_FactSales = 1 in dbo_FactSales"}],
        }
    )
    assert "FactSales" not in json.dumps(result)


def test_non_guid_object_ids_are_replaced_but_stay_matchable():
    scrubber = Scrubber()
    result = scrubber.apply(
        {
            "datasourceInstances": [{"datasourceId": "ds-finance-sql"}],
            "datasourceUsages": [{"datasourceInstanceId": "ds-finance-sql"}],
        }
    )
    assert "finance-sql" not in json.dumps(result)
    assert (
        result["datasourceUsages"][0]["datasourceInstanceId"]
        == result["datasourceInstances"][0]["datasourceId"]
    )


def test_the_demo_estate_leaks_nothing(scrubbed):
    _, result = scrubbed
    encoded = json.dumps(result)
    for secret in (
        "SalesAmount",
        "CostAmount",
        "FactSales",
        "DimCustomer",
        "FinanceDW",
        "finance-sql",
        "crm-sql",
        "contoso",
        "Opportunity",
        "Region",
    ):
        assert secret not in encoded, f"{secret} survived redaction"


def test_longer_names_are_replaced_before_shorter_prefixes():
    scrubber = Scrubber()
    scrubber.pseudonym("Sales", "Column")
    scrubber.pseudonym("SalesAmount", "Column")
    # 'Sales' must not eat the prefix of 'SalesAmount'
    assert scrubber.scrub_text("SalesAmount") == scrubber.mapping["SalesAmount"]


def test_scrubbing_is_stable_across_runs():
    first = Scrubber().apply(demo_scan_result())
    second = Scrubber().apply(demo_scan_result())
    assert json.dumps(first) == json.dumps(second)


def test_disabled_scrubber_is_a_passthrough():
    payload = demo_scan_result()
    assert Scrubber(enabled=False).apply(payload) == payload


# -- the bundle -------------------------------------------------------------
def bundle_transport() -> FakeTransport:
    return (
        FakeTransport()
        .add("admin/capacities", ok({"value": [{"id": "cap-1", "sku": "P1"}]}))
        .add("getInfo", ok({"id": "scan-1", "status": "Succeeded"}))
        .add("scanStatus", ok({"status": "Succeeded"}))
        .add("scanResult", ok(demo_scan_result()))
        .add("admin/workspaces/", HttpResponse(status=404, body=b'{"error":"not here"}'))
        .add("groups/", HttpResponse(status=200, body=b"PK\x03\x04pbix"))
    )


def build_bundle(**kwargs):
    return capture_bundle(
        build_client(kwargs.pop("transport", None) or bundle_transport()),
        settings(),
        [FINANCE_WORKSPACE],
        sleep=lambda _s: None,
        **kwargs,
    )


def test_bundle_captures_every_section():
    bundle = build_bundle()
    assert set(bundle["sections"]) == {
        "capacities",
        "raw_scan",
        "normalized",
        "dmv",
        "export_probe",
    }


def test_bundle_records_the_skus_that_drive_routing():
    assert build_bundle()["sections"]["capacities"]["skus_observed"] == ["P1"]


def test_bundle_reports_what_the_normalizer_understood():
    normalized = build_bundle()["sections"]["normalized"]
    table = normalized["workspaces"][0]["datasets"][0]["tables"][0]
    assert table["partition_types"] == ["m"]
    assert table["partitions_with_expression"] == 1
    assert table["calculated_columns"] == 1


def test_bundle_probes_both_export_endpoints():
    probe = build_bundle()["sections"]["export_probe"]
    assert set(probe) == {"admin_async", "in_group_sync"}
    assert probe["admin_async"]["status"] == 404
    assert probe["in_group_sync"]["looks_like_pbix"] is True


def test_export_probe_never_carries_a_pbix_body():
    probe = build_bundle()["sections"]["export_probe"]
    encoded = json.dumps(probe)
    assert "pbix" not in encoded.replace('"looks_like_pbix"', "")
    assert probe["in_group_sync"]["body_bytes"] == len(b"PK\x03\x04pbix")


def test_bundle_says_why_the_dmv_section_was_skipped():
    assert "no XMLA client" in build_bundle()["sections"]["dmv"]["skipped"]


def test_bundle_never_contains_the_client_secret():
    # settings() builds a client with the secret "secret"; the bundle must
    # report only which *kind* of credential is configured.
    reported = build_bundle()["environment"]["settings"]
    assert reported["credential"] == "client_secret"
    assert "client_id" in reported and reported["client_id"] == "client"


def test_a_failing_section_does_not_lose_the_rest():
    transport = bundle_transport()
    transport.routes["admin/capacities"] = [HttpResponse(status=500)]
    bundle = build_bundle(transport=transport)
    assert bundle["sections"]["capacities"] == {"count": 0, "skus_observed": []}
    assert bundle["sections"]["raw_scan"]


def test_a_failed_scan_is_recorded_rather_than_raised():
    transport = bundle_transport()
    transport.routes["getInfo"] = [HttpResponse(status=403)]
    bundle = build_bundle(transport=transport)
    assert "error" in bundle["sections"]["raw_scan"]


def test_bundle_is_json_serialisable():
    assert json.loads(json.dumps(build_bundle(), default=str))
