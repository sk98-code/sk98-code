"""Build spec §13 tests 4 and 5: destructive and scale.

**Destructive** — delete everything the tool marks unused, then re-analyze
every connected report. Zero broken visuals is the pass condition. The
spec runs this against real files; here it runs against the in-memory
model so it can be automated on every commit, applying the generated
removal plan and re-resolving.

**Scale** — a synthetic 500-workspace / 5000-report tenant: the scan must
complete, respect the rate limit, and resume correctly after a forced kill.
"""

import json

from pbi_lineage.graph import nid_column, nid_measure
from pbi_lineage.removal import plan_removal
from pbi_lineage.resolve import STATUS_UNUSED, analyze_model
from pbi_lineage.schema import (
    Column,
    FieldReference,
    Hierarchy,
    HierarchyLevel,
    Measure,
    Model,
    Page,
    RefKind,
    Relationship,
    ReportLayout,
    ReportLevelMeasure,
    Role,
    Table,
    Visual,
)
from pbi_lineage.service.rest import Response, RestClient, StaticToken, TokenBucket
from pbi_lineage.service.scanner import ScannerClient

# ---------------------------------------------------------------------------
# Destructive test (§13.4)
# ---------------------------------------------------------------------------


def _corpus_model() -> Model:
    """A model containing one instance of each §5.3 trap plus genuinely
    dead objects, so the destructive run has something safe to delete and
    plenty it must refuse to touch."""
    return Model(
        name="Corpus",
        tables=[
            Table(
                name="Sales",
                columns=[
                    Column(name="Qty"),  # projected by a visual
                    Column(name="Region"),  # relationship endpoint
                    Column(name="OrderDate"),  # visual sort-by only
                    Column(name="Segment"),  # visual filter only
                    Column(name="Channel"),  # bookmark filter only
                    Column(name="Margin Basis"),  # report-level measure only
                    Column(name="MonthName", sort_by_column="MonthNo"),
                    Column(name="MonthNo"),  # sort-by target
                    Column(name="RlsCountry"),  # RLS expression only
                    Column(name="DeadColumn"),  # genuinely unused
                    Column(name="AlsoDead"),  # genuinely unused
                ],
                measures=[
                    Measure(name="Total Sales", expression="SUM(Sales[Qty])"),
                    Measure(name="Margin", expression="[Total Sales] * 0.2"),  # transitive
                    Measure(name="Title Text", expression='"Sales for " & [Total Sales]'),
                    Measure(name="Dead Measure", expression="SUM(Sales[DeadColumn])"),
                ],
                hierarchies=[
                    Hierarchy(name="Date Drill", levels=[HierarchyLevel(name="Month", column="MonthName")])
                ],
            ),
            Table(name="Geo", columns=[Column(name="Region"), Column(name="Country")]),
        ],
        relationships=[
            Relationship(
                name="R1", from_table="Sales", from_column="Region", to_table="Geo", to_column="Region"
            )
        ],
        roles=[Role(name="EMEA", table_permissions={"Sales": '[RlsCountry] = "DE"'})],
    )


def _ref(table, name, kind, scope, evidence="fixture"):
    return FieldReference(table=table, name=name, kind=kind, scope=scope, evidence=evidence)


def _corpus_reports() -> list[ReportLayout]:
    main = ReportLayout(
        name="Main",
        pages=[
            Page(
                name="p1",
                visuals=[
                    Visual(
                        name="v1",
                        visual_type="columnChart",
                        references=[
                            _ref("Sales", "Qty", RefKind.COLUMN, "visual:projection"),
                            _ref("Sales", "Margin", RefKind.MEASURE, "visual:projection"),
                            _ref("Sales", "OrderDate", RefKind.COLUMN, "visual:sort"),
                            _ref("Sales", "Segment", RefKind.COLUMN, "visual:filter"),
                            _ref("Sales", "Title Text", RefKind.MEASURE, "visual:formatting"),
                        ],
                    )
                ],
            ),
            # hidden page with a slicer: used, never auto-deleted
            Page(
                name="hidden",
                is_hidden=True,
                visuals=[
                    Visual(
                        name="slicer",
                        visual_type="slicer",
                        references=[
                            _ref("Sales", "Date Drill.Month", RefKind.HIERARCHY_LEVEL, "visual:projection")
                        ],
                    )
                ],
            ),
        ],
        filters=[_ref("Sales", "Channel", RefKind.COLUMN, "report:bookmark")],
    )
    thin = ReportLayout(
        name="Thin",
        pages=[
            Page(
                name="p1",
                visuals=[
                    Visual(
                        name="v",
                        references=[_ref("Sales", "Adj Margin", RefKind.MEASURE, "visual:projection")],
                    )
                ],
            )
        ],
        report_level_measures=[
            ReportLevelMeasure(name="Adj Margin", table="Sales", expression="SUM(Sales[Margin Basis]) * 0.9")
        ],
    )
    return [main, thin]


def _apply_removal(model: Model, target_ids: list[str]) -> Model:
    """Physically remove the planned objects from an in-memory model."""
    import copy

    result = copy.deepcopy(model)
    for node_id in target_ids:
        kind, _, rest = node_id.partition(":")
        if kind == "column":
            table_name, _, column = rest.partition("[")
            table = result.table(table_name)
            if table:
                table.columns = [c for c in table.columns if c.name != column.rstrip("]")]
        elif kind == "measure":
            for table in result.tables:
                table.measures = [m for m in table.measures if m.name != rest]
    return result


def test_destructive_delete_all_unused_breaks_nothing():
    model = _corpus_model()
    reports = _corpus_reports()
    analysis = analyze_model(model, reports)

    unused = [v.object_id for v in analysis.by_status(STATUS_UNUSED)]
    # the genuinely dead objects are found...
    assert nid_column("Sales", "DeadColumn") in unused
    assert nid_column("Sales", "AlsoDead") in unused
    assert nid_measure("Dead Measure") in unused
    # ...and every trap survives as Used
    for protected in (
        nid_column("Sales", "Qty"),
        nid_column("Sales", "Region"),
        nid_column("Sales", "OrderDate"),
        nid_column("Sales", "Segment"),
        nid_column("Sales", "Channel"),
        nid_column("Sales", "Margin Basis"),
        nid_column("Sales", "MonthName"),
        nid_column("Sales", "MonthNo"),
        nid_column("Sales", "RlsCountry"),
        nid_column("Geo", "Region"),
        nid_measure("Total Sales"),
        nid_measure("Margin"),
        nid_measure("Title Text"),
    ):
        assert protected not in unused, f"false positive: {protected} would have been deleted"

    plan = plan_removal(analysis.graph, analysis.verdicts, unused, model=model)
    assert not plan.blocked, plan.block_reasons

    # apply, then re-analyze every connected report
    pruned = _apply_removal(model, plan.targets)
    after = analyze_model(pruned, reports)

    # the pass condition: zero broken references anywhere
    assert after.unresolved == [], [f"{r.table}[{r.name}] via {r.scope}" for r in after.unresolved]
    # and nothing new became unused (no cascade of orphans)
    assert not after.by_status(STATUS_UNUSED)


def test_destructive_run_is_idempotent():
    model = _corpus_model()
    reports = _corpus_reports()
    analysis = analyze_model(model, reports)
    unused = [v.object_id for v in analysis.by_status(STATUS_UNUSED)]
    pruned = _apply_removal(model, unused)
    second = analyze_model(pruned, reports)
    # a second pass finds nothing further to remove
    assert [v.object_id for v in second.by_status(STATUS_UNUSED)] == []


# ---------------------------------------------------------------------------
# Scale test (§13.5)
# ---------------------------------------------------------------------------


def _synthetic_tenant(workspaces: int, reports_per_workspace: int) -> dict:
    return {
        "workspaces": [
            {
                "id": f"ws{w}",
                "name": f"Workspace {w}",
                "datasets": [
                    {
                        "id": f"ds{w}",
                        "name": f"Model {w}",
                        "tables": [{"name": "Sales", "columns": [{"name": "Qty"}]}],
                    }
                ],
                "reports": [
                    {"id": f"r{w}_{r}", "name": f"Report {r}", "datasetId": f"ds{w}"}
                    for r in range(reports_per_workspace)
                ],
            }
            for w in range(workspaces)
        ]
    }


class _ScaleTransport:
    def __init__(self, tenant, *, dead_after: int | None = None):
        self.tenant = tenant
        self.dead_after = dead_after
        self.getinfo_calls = 0
        self.batch = 0

    def request(self, method, url, *, headers, json=None):
        if "getInfo" in url:
            self.getinfo_calls += 1
            if self.dead_after is not None and self.getinfo_calls > self.dead_after:
                return Response(status=503)  # simulated kill / outage
            self.batch = self.getinfo_calls
            return Response(status=200, body={"id": f"scan{self.batch}"})
        if "scanStatus" in url:
            return Response(status=200, body={"status": "Succeeded"})
        if "scanResult" in url:
            # each batch returns its slice of the synthetic tenant
            start = (self.batch - 1) * 100
            return Response(status=200, body={"workspaces": self.tenant["workspaces"][start : start + 100]})
        if "admin/groups" in url:
            return Response(status=200, body={"value": []})
        return Response(status=404, body={})


def test_scale_500_workspaces_completes_and_respects_rate_limit(tmp_path):
    tenant = _synthetic_tenant(500, 10)  # 500 workspaces, 5000 reports
    slept = []
    clock = {"t": 0.0}

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    transport = _ScaleTransport(tenant)
    # a deliberately tight budget so the limiter must engage within the run
    bucket = TokenBucket(3, 3600.0, sleep=sleep, clock=lambda: clock["t"])
    client = RestClient(transport, StaticToken("t"), bucket=bucket, sleep=lambda s: None)
    scanner = ScannerClient(client, tmp_path, sleep=lambda s: None)

    outcome = scanner.scan([f"ws{i}" for i in range(500)])
    assert outcome.complete
    assert outcome.batches_run == 5  # 500 / 100 per call
    assert transport.getinfo_calls == 5
    assert slept, "the rate limiter must pace the run once the bucket drains"

    workspaces = list(scanner.iter_workspaces())
    assert len(workspaces) == 500
    total_reports = sum(len(w["reports"]) for w in workspaces)
    assert total_reports == 5000


def test_scale_scan_resumes_after_forced_kill(tmp_path):
    tenant = _synthetic_tenant(500, 2)
    workspace_ids = [f"ws{i}" for i in range(500)]

    # first run dies after 2 successful batches
    dying = _ScaleTransport(tenant, dead_after=2)
    first = ScannerClient(
        RestClient(dying, StaticToken("t"), sleep=lambda s: None), tmp_path, sleep=lambda s: None
    ).scan(workspace_ids)
    assert not first.complete
    assert first.batches_run == 2
    state = json.loads((tmp_path / "scan_state.json").read_text())
    assert sorted(state["completed_batches"]) == [0, 1]

    # resume: only the outstanding batches run, and the result is whole
    healthy = ScannerClient(
        RestClient(_ScaleTransport(tenant), StaticToken("t"), sleep=lambda s: None),
        tmp_path,
        sleep=lambda s: None,
    )
    second = healthy.scan(workspace_ids)
    assert second.batches_skipped == 2
    assert second.batches_run == 3
    assert second.complete
    assert len(list(healthy.iter_workspaces())) == 500
