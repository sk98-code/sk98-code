"""Milestones 7, 7b, 8: XMLA/Service mode, thin report engine, Scanner API.

Includes every fixture §5.4.8 requires of the thin-report engine.
"""

from pbi_lineage.readers.rdl import rdl_references, read_rdl
from pbi_lineage.resolve import STATUS_EXTERNAL, STATUS_INCOMPLETE, STATUS_UNUSED, STATUS_USED, analyze_model
from pbi_lineage.graph import nid_column
from pbi_lineage.schema import Column, Measure, Model, Table
from pbi_lineage.service.rest import Response, RestClient, RetryPolicy, StaticToken, TokenBucket
from pbi_lineage.service.scanner import ScannerClient, model_from_dataset_schema
from pbi_lineage.service.thin_reports import (
    Consumer,
    ConsumerType,
    FailureCause,
    ReportFetcher,
    build_consumer_index,
    detect_excel_consumers,
    gather_model_usage,
    similar_reports,
    synthetic_layout,
)
from pbi_lineage.service.xmla import (
    Fidelity,
    Mode,
    WorkspaceCapability,
    decide_source,
    workspace_capability_from_scanner,
    xmla_connection_string,
)

from .pbil_fixtures import alias_ref, layout, section, visual_container, write_pbix

# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


class FakeTransport:
    def __init__(self, handlers):
        self.handlers = handlers  # list of (predicate, Response) or callable
        self.calls = []

    def request(self, method, url, *, headers, json=None):
        self.calls.append((method, url, json))
        for predicate, response in self.handlers:
            if predicate(method, url):
                return response(method, url, json) if callable(response) else response
        return Response(status=404, body={"error": {"message": "no handler"}})


def _client(handlers, **kwargs):
    return RestClient(FakeTransport(handlers), StaticToken("t"), sleep=lambda s: None, **kwargs)


# ---------------------------------------------------------------------------
# M7: XMLA / mode selection
# ---------------------------------------------------------------------------


def test_premium_workspace_gets_xmla_full_fidelity():
    workspace = WorkspaceCapability(
        workspace_id="w1",
        name="Finance",
        capacity_id="c1",
        capacity_sku="P1",
        is_on_dedicated_capacity=True,
    )
    decision = decide_source(workspace, dataset="Sales Model")
    assert workspace.has_xmla
    assert decision.mode is Mode.M3_SHARED_ONLINE
    assert decision.fidelity is Fidelity.FULL
    assert decision.size_analysis_available
    assert "powerbi://api.powerbi.com/v1.0/myorg/Finance" in decision.connection_string
    assert "Initial Catalog=Sales Model" in decision.connection_string
    assert decision.notices == []


def test_pro_workspace_is_explicitly_degraded_not_silent():
    # §3 critical constraint: no XMLA on Pro
    workspace = WorkspaceCapability(workspace_id="w2", name="Team Pro")
    decision = decide_source(workspace)
    assert not workspace.has_xmla
    assert decision.mode is Mode.M4_PRO_WORKSPACE
    assert decision.fidelity is Fidelity.REDUCED
    assert not decision.size_analysis_available
    assert decision.connection_string is None
    assert decision.notices and "reduced-confidence" in decision.notices[0]


def test_capability_from_scanner_payload_and_write_endpoint():
    workspace = workspace_capability_from_scanner(
        {"id": "w3", "name": "Fab", "capacityId": "c9", "capacitySku": "F64", "isOnDedicatedCapacity": True}
    )
    assert workspace.has_xmla
    assert "Mode=ReadWrite" in xmla_connection_string("Fab", readwrite=True)


def test_dataset_schema_gives_degraded_model_for_pro():
    model = model_from_dataset_schema(
        {
            "name": "ProModel",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [{"name": "Qty", "dataType": "Int64"}],
                    "measures": [{"name": "Total", "expression": "SUM(Sales[Qty])"}],
                }
            ],
        }
    )
    assert model.table("Sales").columns[0].name == "Qty"
    assert model.table("Sales").measures[0].expression == "SUM(Sales[Qty])"


# ---------------------------------------------------------------------------
# REST plumbing
# ---------------------------------------------------------------------------


def test_retry_honours_retry_after_then_succeeds():
    calls = {"n": 0}

    def handler(method, url, body):
        calls["n"] += 1
        if calls["n"] == 1:
            return Response(status=429, headers={"Retry-After": "7"})
        return Response(status=200, body={"ok": True})

    client = _client([(lambda m, u: True, handler)], retry=RetryPolicy(max_attempts=3))
    response = client.get("admin/groups")
    assert response.ok
    assert client.throttle_waits == [7.0]


def test_non_retryable_4xx_returns_immediately():
    client = _client([(lambda m, u: True, Response(status=403, body={}))])
    response = client.get("admin/groups")
    assert response.status == 403
    assert client.request_count == 1


def test_token_bucket_paces_requests():
    slept = []
    clock = {"t": 0.0}

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    bucket = TokenBucket(2, 60.0, sleep=sleep, clock=lambda: clock["t"])
    bucket.take()
    bucket.take()
    bucket.take()  # third exceeds capacity → must wait
    assert slept and slept[0] > 0


# ---------------------------------------------------------------------------
# M7b: thin report engine
# ---------------------------------------------------------------------------


def _shared_model():
    return Model(
        name="Shared",
        tables=[
            Table(
                name="Sales",
                columns=[Column(name="Qty"), Column(name="DiscountCode"), Column(name="Segment")],
                measures=[Measure(name="Total Sales", expression="SUM(Sales[Qty])")],
            )
        ],
    )


def _scan_payload():
    return {
        "workspaces": [
            {
                "id": "ws1",
                "name": "Finance",
                "reports": [
                    {"id": "r1", "name": "EMEA Margin", "datasetId": "ds1", "datasetWorkspaceId": "ws2"},
                    {"id": "r2", "name": "Ops", "datasetId": "ds1", "datasetWorkspaceId": "ws2"},
                    {"id": "r3", "name": "Billing", "datasetId": "ds1", "reportType": "PaginatedReport"},
                    {"id": "r4", "name": "Usage Metrics Report", "datasetId": "ds1"},
                ],
                "datasets": [
                    {"id": "ds9", "name": "Composite", "upstreamDatasets": [{"targetDatasetId": "ds1"}]}
                ],
                "notebooks": [
                    {"id": "nb1", "name": "sempy explore", "upstreamDatasets": [{"targetDatasetId": "ds1"}]}
                ],
            }
        ]
    }


def test_consumer_index_covers_every_consumer_type():
    index = build_consumer_index(_scan_payload())
    consumers = index["ds1"]
    types = {c.consumer_type for c in consumers}
    assert ConsumerType.THIN_REPORT in types
    assert ConsumerType.PAGINATED in types
    assert ConsumerType.COMPOSITE_MODEL in types
    assert ConsumerType.NOTEBOOK in types
    assert len(consumers) == 6


def _pbix_bytes(tmp_path, name, refs):
    """Build a thin-report PBIX whose visual projects `refs`."""
    select = [
        {"Column": {"Expression": alias_ref("s"), "Property": ref}, "Name": f"Sales.{ref}"} for ref in refs
    ]
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
            "Select": select,
        }
    )
    path = write_pbix(tmp_path / f"{name}.pbix", layout(sections=[section(visual_containers=[container])]))
    return path.read_bytes()


class StubFetcher(ReportFetcher):
    """Real ReportFetcher driven by a fake RestClient, so the production
    failure-classification path is what the tests exercise."""

    def __init__(self, content_by_id, failures=None):
        self.content_by_id = content_by_id
        self.failures = failures or {}
        super().__init__(_client([(lambda m, u: True, self._respond)]))

    def _respond(self, method, url, body):
        report_id = url.rstrip("/").split("/reports/")[-1].split("/")[0]
        if report_id in self.failures:
            status, message = self.failures[report_id]
            return Response(status=status, body={"error": {"message": message}})
        return Response(status=200, content=self.content_by_id[report_id])


def _thin(consumer_id, name="R"):
    return Consumer(
        id=consumer_id,
        name=name,
        workspace_id="ws1",
        consumer_type=ConsumerType.THIN_REPORT,
        dataset_id="ds1",
    )


def test_union_across_two_thin_reports_and_complete_coverage(tmp_path):
    # §5.4.8 fixture 3: each report alone looks like it leaves a column unused
    consumers = [_thin("a", "Report A"), _thin("b", "Report B")]
    fetcher = StubFetcher(
        {
            "a": _pbix_bytes(tmp_path, "a", ["Qty"]),
            "b": _pbix_bytes(tmp_path, "b", ["DiscountCode"]),
        }
    )
    layouts, extra, coverage = gather_model_usage("ds1", consumers, fetcher, _shared_model())
    assert coverage.complete
    assert len(layouts) == 2
    result = analyze_model(_shared_model(), layouts, coverage_complete=coverage.complete)
    assert result.verdicts[nid_column("Sales", "Qty")].status == STATUS_USED
    assert result.verdicts[nid_column("Sales", "DiscountCode")].status == STATUS_USED
    assert result.verdicts[nid_column("Sales", "Segment")].status == STATUS_UNUSED


def test_export_failure_blocks_all_verdicts(tmp_path):
    # §5.4.8 fixture 4: the completeness gate must block, not warn
    consumers = [_thin("a", "Report A"), _thin("b", "Sensitive")]
    fetcher = StubFetcher(
        {"a": _pbix_bytes(tmp_path, "a", ["Qty"])},
        failures={"b": (403, "Report has a sensitivity label with encryption")},
    )
    layouts, extra, coverage = gather_model_usage("ds1", consumers, fetcher, _shared_model())
    assert not coverage.complete
    assert coverage.summary() == "1 of 2 connected reports analyzed — 1 could not be retrieved"
    failure = coverage.failure_report()[0]
    assert failure["cause"] == FailureCause.SENSITIVITY_LABEL.value
    assert failure["report_name"] == "Sensitive"

    result = analyze_model(_shared_model(), layouts, coverage_complete=coverage.complete)
    verdict = result.verdicts[nid_column("Sales", "DiscountCode")]
    assert verdict.status == STATUS_INCOMPLETE
    assert not verdict.deletable


def test_analyze_in_excel_reduces_confidence(tmp_path):
    # §5.4.8 fixture 7
    events = [
        {
            "Activity": "AnalyzedByExternalApplication",
            "DatasetId": "ds1",
            "UserId": "u1",
            "WorkspaceId": "ws1",
        },
        {"Activity": "AnalyzedByExternalApplication", "DatasetId": "ds1", "UserId": "u1"},  # dedup
        {"Activity": "ViewReport", "DatasetId": "ds1", "UserId": "u2"},
    ]
    excel = detect_excel_consumers(events, "ds1")
    assert len(excel) == 1
    consumers = [_thin("a", "Report A"), *excel]
    fetcher = StubFetcher({"a": _pbix_bytes(tmp_path, "a", ["Qty"])})
    layouts, extra, coverage = gather_model_usage("ds1", consumers, fetcher, _shared_model())
    assert coverage.complete  # AiE is not a retrieval failure...
    assert coverage.external_consumer_labels  # ...but it caps confidence
    result = analyze_model(
        _shared_model(),
        layouts,
        coverage_complete=coverage.complete,
        external_consumers=coverage.external_consumer_labels,
    )
    assert result.verdicts[nid_column("Sales", "Segment")].status == STATUS_EXTERNAL


def test_paginated_report_measure_reference_trap():
    # §5.4.8 fixture 6: measure referenced as Table[Measure] in query text
    rdl_xml = """<?xml version="1.0"?>
    <Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition">
      <DataSets>
        <DataSet Name="ds">
          <Query>
            <CommandType>Text</CommandType>
            <CommandText>EVALUATE SUMMARIZECOLUMNS(Sales[DiscountCode], "T", Sales[Total Sales])</CommandText>
          </Query>
        </DataSet>
      </DataSets>
    </Report>"""
    report = read_rdl(rdl_xml, name="Billing")
    references = rdl_references(report, _shared_model())
    kinds = {(r.name, r.kind.value) for r in references}
    # the trap: Sales[Total Sales] is a MEASURE, not a column
    assert ("Total Sales", "measure") in kinds
    assert ("DiscountCode", "column") in kinds


def test_paginated_consumer_contributes_usage(tmp_path):
    consumers = [
        Consumer(
            id="p1",
            name="Billing",
            workspace_id="ws1",
            consumer_type=ConsumerType.PAGINATED,
            dataset_id="ds1",
        )
    ]
    rdl = b"""<Report><DataSets><DataSet Name="d"><Query>
        <CommandText>EVALUATE SUMMARIZECOLUMNS(Sales[Segment])</CommandText></Query></DataSet></DataSets></Report>"""
    fetcher = StubFetcher({"p1": rdl})
    layouts, extra, coverage = gather_model_usage("ds1", consumers, fetcher, _shared_model())
    assert coverage.complete
    assert any(r.name == "Segment" for r in extra)
    result = analyze_model(
        _shared_model(), [synthetic_layout("paginated", extra)], coverage_complete=coverage.complete
    )
    assert result.verdicts[nid_column("Sales", "Segment")].status == STATUS_USED


def test_composite_model_and_notebook_cap_confidence(tmp_path):
    # §5.4.8 fixture 5: a composite model chained on the shared model
    consumers = [
        _thin("a", "Report A"),
        Consumer(id="ds9", name="Composite", workspace_id="ws1", consumer_type=ConsumerType.COMPOSITE_MODEL),
    ]
    fetcher = StubFetcher({"a": _pbix_bytes(tmp_path, "a", ["Qty"])})
    layouts, extra, coverage = gather_model_usage("ds1", consumers, fetcher, _shared_model())
    assert coverage.complete
    assert any(c.consumer_type is ConsumerType.COMPOSITE_MODEL for c in coverage.unparseable_consumers)


def test_usage_metrics_report_is_skipped(tmp_path):
    consumers = [_thin("a", "Report A"), _thin("um", "Usage Metrics Report")]
    fetcher = StubFetcher({"a": _pbix_bytes(tmp_path, "a", ["Qty"])})
    layouts, extra, coverage = gather_model_usage("ds1", consumers, fetcher, _shared_model())
    assert [c.name for c in coverage.skipped] == ["Usage Metrics Report"]
    assert coverage.complete


def test_report_similarity_finds_forks(tmp_path):
    from pbi_lineage.readers.pbix import read_pbix_bytes

    a = read_pbix_bytes(_pbix_bytes(tmp_path, "a", ["Qty", "Segment"]), name="A").report
    b = read_pbix_bytes(_pbix_bytes(tmp_path, "b", ["Qty", "Segment"]), name="B").report
    c = read_pbix_bytes(_pbix_bytes(tmp_path, "c", ["DiscountCode"]), name="C").report
    pairs = similar_reports([a, b, c])
    assert pairs and pairs[0][:2] == ("A", "B")
    assert pairs[0][2] == 1.0


# ---------------------------------------------------------------------------
# M8: Scanner API
# ---------------------------------------------------------------------------


def _scanner_handlers(scan_payload, *, fail_workspace=None):
    state = {"posts": 0}

    def get_info(method, url, body):
        state["posts"] += 1
        # a persistently failing batch: every attempt for it returns 500,
        # so the retry policy exhausts and the batch stays incomplete
        if fail_workspace is not None and fail_workspace in (body or {}).get("workspaces", []):
            return Response(status=500)
        return Response(status=200, body={"id": f"scan{state['posts']}"})

    return [
        (lambda m, u: "getInfo" in u, get_info),
        (lambda m, u: "scanStatus" in u, Response(status=200, body={"status": "Succeeded"})),
        (lambda m, u: "scanResult" in u, Response(status=200, body=scan_payload)),
        (lambda m, u: "workspaces/modified" in u, Response(status=200, body=[{"id": "w1"}, {"id": "w2"}])),
        (lambda m, u: "admin/groups" in u, Response(status=200, body={"value": []})),
    ]


def test_scan_batches_by_100_and_persists_each_batch(tmp_path):
    client = _client(_scanner_handlers(_scan_payload()))
    scanner = ScannerClient(client, tmp_path, sleep=lambda s: None)
    workspace_ids = [f"w{i}" for i in range(250)]
    outcome = scanner.scan(workspace_ids)
    assert outcome.batches_run == 3  # 100 + 100 + 50
    assert outcome.complete
    assert (tmp_path / "scan_state.json").exists()
    assert len(list(tmp_path.glob("scan_batch_*.json"))) == 3
    # results stream back without loading everything at once
    workspaces = list(scanner.iter_workspaces())
    assert len(workspaces) == 3  # one workspace per batch payload


def test_scan_resumes_after_forced_kill(tmp_path):
    payload = _scan_payload()
    # first run: the batch containing w100 (batch index 1) fails every attempt
    client = _client(_scanner_handlers(payload, fail_workspace="w100"))
    scanner = ScannerClient(client, tmp_path, sleep=lambda s: None)
    workspace_ids = [f"w{i}" for i in range(250)]
    first = scanner.scan(workspace_ids)
    assert first.batches_run == 2
    assert not first.complete
    assert first.warnings

    # second run: same ids, healthy transport → only the missing batch runs
    scanner2 = ScannerClient(_client(_scanner_handlers(payload)), tmp_path, sleep=lambda s: None)
    second = scanner2.scan(workspace_ids)
    assert second.batches_skipped == 2
    assert second.batches_run == 1
    assert second.complete


def test_scan_respects_rate_limit_budget(tmp_path):
    slept = []
    clock = {"t": 0.0}

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    bucket = TokenBucket(2, 3600.0, sleep=sleep, clock=lambda: clock["t"])
    client = RestClient(
        FakeTransport(_scanner_handlers(_scan_payload())),
        StaticToken("t"),
        bucket=bucket,
        sleep=lambda s: None,
    )
    scanner = ScannerClient(client, tmp_path, sleep=lambda s: None)
    scanner.scan([f"w{i}" for i in range(300)])
    assert slept, "rate limiter must pace calls once the bucket is drained"


def test_preflight_reports_service_principal_setup_failure(tmp_path):
    client = _client([(lambda m, u: True, Response(status=403, body={}))])
    ok, message = ScannerClient(client, tmp_path).preflight()
    assert not ok
    assert "service principals" in message


def test_modified_since_incremental_listing(tmp_path):
    client = _client(_scanner_handlers(_scan_payload()))
    scanner = ScannerClient(client, tmp_path, sleep=lambda s: None)
    ids = scanner.list_workspace_ids(modified_since="2026-01-01T00:00:00Z")
    assert ids == ["w1", "w2"]
    assert any("modifiedSince=2026-01-01" in call[1] for call in client.transport.calls)
