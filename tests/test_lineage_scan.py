"""Admin API client, retry policy, incremental state and the scan orchestrator.

Everything here runs against a fake transport — no network, no tenant.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from pbilineage.auth.credentials import StaticTokenProvider
from pbilineage.clients.admin_api import PowerBIAdminClient, chunked
from pbilineage.clients.http import ApiError, HttpResponse, RetryPolicy, request_with_retry
from pbilineage.config import Settings, load_dotenv
from pbilineage.demo.fixtures import (
    FINANCE_WORKSPACE,
    SALES_WORKSPACE,
    demo_scan_result,
)
from pbilineage.graph.store import InMemoryGraphStore
from pbilineage.scan.orchestrator import ScanOrchestrator
from pbilineage.scan.state import ScanState, api_timestamp, content_hash


class FakeTransport:
    """Serves canned responses and records what was asked for."""

    def __init__(self, routes: dict[str, list[HttpResponse]] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, str]] = []

    def add(self, fragment: str, *responses: HttpResponse) -> FakeTransport:
        self.routes.setdefault(fragment, []).extend(responses)
        return self

    def send(self, method, url, headers, body=None, timeout=120.0) -> HttpResponse:
        self.calls.append((method, url))
        for fragment, queued in self.routes.items():
            if fragment in url:
                if len(queued) > 1:
                    return queued.pop(0)
                return queued[0]
        return HttpResponse(status=404, body=b'{"error":"no route"}')


def ok(payload) -> HttpResponse:
    return HttpResponse(status=200, body=json.dumps(payload).encode("utf-8"))


def settings() -> Settings:
    return Settings(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        scan_poll_seconds=0.0,
        backoff_base_seconds=0.0,
        max_retries=3,
    )


def build_client(transport: FakeTransport) -> PowerBIAdminClient:
    return PowerBIAdminClient(
        settings(),
        StaticTokenProvider(),
        transport,
        RetryPolicy(max_attempts=3, base_seconds=0.0, sleep=lambda _s: None),
    )


# -- retry policy -----------------------------------------------------------
def test_successful_request_is_not_retried():
    transport = FakeTransport().add("thing", ok({"ok": True}))
    policy = RetryPolicy(max_attempts=3, base_seconds=0.0, sleep=lambda _s: None)
    request_with_retry(transport, policy, "GET", "https://x/thing", {})
    assert len(transport.calls) == 1


def test_throttling_is_retried_then_succeeds():
    transport = FakeTransport().add(
        "thing",
        HttpResponse(status=429, headers={"Retry-After": "0"}),
        ok({"ok": True}),
    )
    policy = RetryPolicy(max_attempts=3, base_seconds=0.0, sleep=lambda _s: None)
    response = request_with_retry(transport, policy, "GET", "https://x/thing", {})
    assert response.ok
    assert len(transport.calls) == 2


def test_client_errors_are_not_retried():
    transport = FakeTransport().add("thing", HttpResponse(status=401, body=b"nope"))
    policy = RetryPolicy(max_attempts=3, base_seconds=0.0, sleep=lambda _s: None)
    response = request_with_retry(transport, policy, "GET", "https://x/thing", {})
    assert response.status == 401
    assert len(transport.calls) == 1


def test_persistent_server_errors_raise_after_the_last_attempt():
    transport = FakeTransport().add("thing", HttpResponse(status=503))
    policy = RetryPolicy(max_attempts=3, base_seconds=0.0, sleep=lambda _s: None)
    with pytest.raises(ApiError, match="after 3 attempts"):
        request_with_retry(transport, policy, "GET", "https://x/thing", {})
    assert len(transport.calls) == 3


def test_retry_after_header_is_honoured():
    policy = RetryPolicy(base_seconds=10.0, sleep=lambda _s: None)
    assert policy.delay_for(1, retry_after="2") == 2.0


def test_backoff_grows_and_is_capped():
    policy = RetryPolicy(base_seconds=2.0, max_seconds=8.0, sleep=lambda _s: None)
    assert policy.delay_for(1) <= 2.0
    assert policy.delay_for(9) <= 8.0


# -- admin client -----------------------------------------------------------
def test_workspace_batches_respect_the_scanner_limit():
    assert [len(batch) for batch in chunked([str(i) for i in range(250)])] == [100, 100, 50]


def test_start_scan_refuses_an_oversized_batch():
    with pytest.raises(ValueError, match="at most 100"):
        build_client(FakeTransport()).start_scan([str(i) for i in range(101)])


def test_modified_workspaces_returns_ids():
    transport = FakeTransport().add("workspaces/modified", ok([{"id": "a"}, {"id": "b"}, {"nope": 1}]))
    assert build_client(transport).get_modified_workspaces() == ["a", "b"]


def test_modified_since_is_passed_through():
    transport = FakeTransport().add("workspaces/modified", ok([]))
    build_client(transport).get_modified_workspaces("2026-08-01T00:00:00.0000000Z")
    assert "modifiedSince=2026-08-01T00:00:00.0000000Z" in transport.calls[0][1]


def test_a_full_inventory_call_omits_modified_since():
    transport = FakeTransport().add("workspaces/modified", ok([]))
    build_client(transport).get_modified_workspaces()
    assert "modifiedSince" not in transport.calls[0][1]


def test_scan_flags_request_schema_and_expressions():
    transport = (
        FakeTransport()
        .add("getInfo", ok({"id": "scan-1", "status": "Succeeded"}))
        .add("scanStatus", ok({"status": "Succeeded"}))
        .add("scanResult", ok(demo_scan_result()))
    )
    build_client(transport).scan_workspaces([FINANCE_WORKSPACE], sleep=lambda _s: None)
    get_info_url = next(url for method, url in transport.calls if "getInfo" in url)
    assert "datasetSchema=true" in get_info_url
    assert "datasetExpressions=true" in get_info_url
    assert "lineage=true" in get_info_url


def test_scan_polls_until_it_succeeds():
    transport = (
        FakeTransport()
        .add("getInfo", ok({"id": "scan-1", "status": "Running"}))
        .add(
            "scanStatus",
            ok({"status": "Running"}),
            ok({"status": "Running"}),
            ok({"status": "Succeeded"}),
        )
        .add("scanResult", ok(demo_scan_result()))
    )
    results = build_client(transport).scan_workspaces([FINANCE_WORKSPACE], sleep=lambda _s: None)
    assert len(results) == 1
    assert len([1 for _m, url in transport.calls if "scanStatus" in url]) == 3


def test_failed_scan_raises_with_the_status():
    transport = (
        FakeTransport()
        .add("getInfo", ok({"id": "scan-1", "status": "Running"}))
        .add("scanStatus", ok({"status": "Failed"}))
    )
    with pytest.raises(ApiError, match="status Failed"):
        build_client(transport).scan_workspaces([FINANCE_WORKSPACE], sleep=lambda _s: None)


def test_capacity_skus_are_mapped():
    transport = FakeTransport().add("admin/capacities", ok({"value": [{"id": "cap-1", "sku": "P1"}]}))
    assert build_client(transport).get_capacity_skus() == {"cap-1": "P1"}


def test_missing_capacity_endpoint_is_not_fatal():
    transport = FakeTransport().add("admin/capacities", HttpResponse(status=403))
    assert build_client(transport).get_capacity_skus() == {}


def test_unexportable_report_returns_none_rather_than_raising(tmp_path):
    transport = FakeTransport().add("Export", HttpResponse(status=403))
    result = build_client(transport).export_report("ws", "report", tmp_path / "r.pbix", sleep=lambda _s: None)
    assert result is None


def test_report_export_falls_back_to_the_in_group_endpoint(tmp_path):
    transport = (
        FakeTransport()
        .add("admin/workspaces", HttpResponse(status=404))
        .add("groups/ws/reports", HttpResponse(status=200, body=b"PK\x03\x04pbix"))
    )
    path = build_client(transport).export_report("ws", "report", tmp_path / "r.pbix", sleep=lambda _s: None)
    assert path is not None and path.read_bytes().startswith(b"PK")


# -- scan state -------------------------------------------------------------
def test_checkpoint_detects_content_change(tmp_path):
    with ScanState(tmp_path / "state.db") as state:
        assert state.record("ws1", payload_hash="abc") is True
        assert state.record("ws1", payload_hash="abc") is False
        assert state.record("ws1", payload_hash="def") is True


def test_watermark_is_the_oldest_successful_checkpoint(tmp_path):
    now = datetime.now(timezone.utc)
    with ScanState(tmp_path / "state.db") as state:
        state.record("ws1", scanned_at=now)
        state.record("ws2", scanned_at=now - timedelta(hours=2))
        assert state.last_successful_scan() == now - timedelta(hours=2)


def test_a_stale_watermark_degrades_to_a_full_scan(tmp_path):
    with ScanState(tmp_path / "state.db") as state:
        state.record("ws1", scanned_at=datetime.now(timezone.utc) - timedelta(days=90))
        assert state.modified_since() == ""


def test_a_recent_watermark_produces_an_api_timestamp(tmp_path):
    with ScanState(tmp_path / "state.db") as state:
        state.record("ws1", scanned_at=datetime.now(timezone.utc) - timedelta(hours=1))
        assert state.modified_since().endswith("Z")


def test_failed_workspaces_are_retried_next_run(tmp_path):
    with ScanState(tmp_path / "state.db") as state:
        state.record("ws1", status="failed", detail="XMLA refused")
        assert state.failed_workspaces() == ["ws1"]


def test_run_log_records_outcomes(tmp_path):
    with ScanState(tmp_path / "state.db") as state:
        run_id = state.start_run("full")
        state.finish_run(run_id, 3, "succeeded")
        assert state.recent_runs()[0]["status"] == "succeeded"


def test_content_hash_is_order_independent():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_api_timestamp_format():
    stamp = api_timestamp(datetime(2026, 8, 1, 12, 30, 0, tzinfo=timezone.utc))
    assert stamp == "2026-08-01T12:30:00.0000000Z"


# -- orchestrator -----------------------------------------------------------
def full_scan_transport() -> FakeTransport:
    return (
        FakeTransport()
        .add("workspaces/modified", ok([{"id": FINANCE_WORKSPACE}, {"id": SALES_WORKSPACE}]))
        .add("admin/capacities", ok({"value": [{"id": "cap-premium-p1", "sku": "P1"}]}))
        .add("getInfo", ok({"id": "scan-1", "status": "Succeeded"}))
        .add("scanStatus", ok({"status": "Succeeded"}))
        .add("scanResult", ok(demo_scan_result()))
        .add("dataflows", HttpResponse(status=404))
        .add("Export", HttpResponse(status=404))
    )


def run_scan(tmp_path, **kwargs):
    transport = kwargs.pop("transport", None) or full_scan_transport()
    store = kwargs.pop("store", None) or InMemoryGraphStore()
    with ScanState(tmp_path / "state.db") as state:
        orchestrator = ScanOrchestrator(settings(), build_client(transport), None, state, store)
        report = orchestrator.run(sleep=lambda _s: None, **kwargs)
    return report, store


def test_full_scan_builds_a_graph(tmp_path):
    report, store = run_scan(tmp_path)
    assert report.mode == "full"
    assert report.workspaces_scanned == 2
    assert report.datasets == 2
    assert store.stats()["nodes"] > 0


def test_scan_without_xmla_routes_everything_to_the_parser(tmp_path):
    report, _ = run_scan(tmp_path)
    # Sales is Pro so the parser is its only path; Finance is on P1 capacity,
    # so it is recorded as having fallen back rather than as a plain choice.
    assert set(report.routing) == {"dax-parser", "dax-parser+fallback"}


def test_reports_that_will_not_export_still_yield_model_lineage(tmp_path):
    report, store = run_scan(tmp_path)
    assert report.reports == 2
    assert report.reports_with_layout == 0
    assert any("not exportable" in w for w in report.warnings)
    assert store.stats()["nodes_by_kind"]["Report"] == 2
    assert store.stats()["nodes_by_kind"]["Measure"] > 0


def test_report_export_can_be_skipped(tmp_path):
    report, _ = run_scan(tmp_path, export_reports=False)
    assert not any("not exportable" in w for w in report.warnings)


def test_explicit_workspace_list_skips_the_inventory_call(tmp_path):
    transport = full_scan_transport()
    run_scan(tmp_path, transport=transport, workspace_ids=[FINANCE_WORKSPACE])
    assert not any("workspaces/modified" in url for _m, url in transport.calls)


def test_scan_records_a_checkpoint_per_workspace(tmp_path):
    run_scan(tmp_path)
    with ScanState(tmp_path / "state.db") as state:
        assert set(state.known_workspaces()) == {FINANCE_WORKSPACE, SALES_WORKSPACE}


def test_incremental_scan_uses_the_watermark(tmp_path):
    run_scan(tmp_path)
    transport = full_scan_transport()
    run_scan(tmp_path, transport=transport, incremental=True)
    modified_calls = [url for _m, url in transport.calls if "workspaces/modified" in url]
    assert modified_calls and "modifiedSince=" in modified_calls[0]


def test_incremental_rescan_does_not_duplicate_nodes(tmp_path):
    _, store = run_scan(tmp_path)
    before = store.stats()["nodes"]
    with ScanState(tmp_path / "state.db") as state:
        ScanOrchestrator(settings(), build_client(full_scan_transport()), None, state, store).run(
            incremental=True, sleep=lambda _s: None
        )
    assert store.stats()["nodes"] == before


def test_inventory_failure_is_reported_not_raised(tmp_path):
    transport = FakeTransport().add("workspaces/modified", HttpResponse(status=500))
    report, _ = run_scan(tmp_path, transport=transport)
    assert report.errors and "could not list workspaces" in report.errors[0]


def test_nothing_to_scan_is_a_clean_no_op(tmp_path):
    transport = full_scan_transport()
    transport.routes["workspaces/modified"] = [ok([])]
    report, _ = run_scan(tmp_path, transport=transport)
    assert report.workspaces_scanned == 0
    assert not report.errors


# -- configuration ----------------------------------------------------------
def test_dotenv_does_not_override_the_real_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("PBI_TENANT_ID=from-file\nPBI_CLIENT_ID=also-from-file\n")
    monkeypatch.setenv("PBI_TENANT_ID", "from-environment")
    load_dotenv(env_file)
    loaded = Settings.load(env_file)
    assert loaded.tenant_id == "from-environment"
    assert loaded.client_id == "also-from-file"


def test_redacted_settings_never_include_the_secret():
    configured = Settings(tenant_id="tenant", client_id="client", client_secret="hunter2-do-not-log")
    redacted = configured.redacted()
    assert "hunter2-do-not-log" not in json.dumps(redacted)
    # only the *kind* of credential is reported
    assert redacted["credential"] == "client_secret"


def test_missing_configuration_is_listed_by_name():
    assert Settings().missing() == [
        "PBI_TENANT_ID",
        "PBI_CLIENT_ID",
        "PBI_CLIENT_SECRET (or PBI_CERTIFICATE_PATH + PBI_CERTIFICATE_THUMBPRINT)",
    ]
