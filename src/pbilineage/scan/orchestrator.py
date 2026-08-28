"""The scan run loop.

Full scan:      inventory -> Scanner API -> route per workspace -> build graph
Incremental:    GetModifiedWorkspaces -> same, for changed workspaces only

Failures are contained at the workspace level. One workspace whose XMLA
endpoint is unreachable, or whose report will not export, must not cost the
tenant its scan — it degrades that workspace's confidence and is recorded in
the report's warnings.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pbilineage.clients.admin_api import PowerBIAdminClient
from pbilineage.clients.http import ApiError
from pbilineage.clients.xmla import XmlaClient, XmlaUnavailable
from pbilineage.config import Settings
from pbilineage.graph.builder import GraphBuilder
from pbilineage.graph.store import InMemoryGraphStore
from pbilineage.models import (
    DatasetSpec,
    LineageGraph,
    ReportSpec,
    TenantSnapshot,
    WorkspaceSpec,
)
from pbilineage.parsers.layout import parse_pbix_layout
from pbilineage.resolve.base import DependencyResult
from pbilineage.resolve.router import CapacityRouter
from pbilineage.resolve.xmla_resolver import XmlaDependencyResolver
from pbilineage.scan.normalize import (
    dataflow_queries_from_model_json,
    snapshot_from_scan_results,
)
from pbilineage.scan.state import ScanState, content_hash

__all__ = ["ScanOrchestrator", "ScanReport"]


@dataclass(slots=True)
class ScanReport:
    mode: str = "full"
    workspaces_requested: int = 0
    workspaces_scanned: int = 0
    datasets: int = 0
    reports: int = 0
    reports_with_layout: int = 0
    dataflows: int = 0
    routing: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    graph_stats: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "workspaces_requested": self.workspaces_requested,
            "workspaces_scanned": self.workspaces_scanned,
            "datasets": self.datasets,
            "reports": self.reports,
            "reports_with_layout": self.reports_with_layout,
            "dataflows": self.dataflows,
            "routing": self.routing,
            "warnings": self.warnings,
            "errors": self.errors,
            "graph": self.graph_stats,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class ScanOrchestrator:
    """Drives a full or incremental tenant scan and produces a lineage graph."""

    def __init__(
        self,
        settings: Settings,
        admin_client: PowerBIAdminClient,
        xmla_client: XmlaClient | None = None,
        state: ScanState | None = None,
        store: InMemoryGraphStore | None = None,
    ) -> None:
        self.settings = settings
        self.admin = admin_client
        self.xmla = xmla_client
        self.state = state
        self.store = store or InMemoryGraphStore()

    # -- inventory --------------------------------------------------------
    def target_workspaces(self, incremental: bool, explicit: list[str] | None = None) -> list[str]:
        if explicit:
            return list(explicit)
        modified_since = ""
        if incremental and self.state is not None:
            modified_since = self.state.modified_since()
        ids = self.admin.get_modified_workspaces(modified_since)
        if incremental and self.state is not None:
            # Retry anything that failed last time even if it did not change.
            ids = sorted(set(ids) | set(self.state.failed_workspaces()))
        return ids

    # -- the run ----------------------------------------------------------
    def run(
        self,
        workspace_ids: list[str] | None = None,
        incremental: bool = False,
        export_reports: bool | None = None,
        sleep=None,
    ) -> ScanReport:
        report = ScanReport(mode="incremental" if incremental else "full")
        run_id = self.state.start_run(report.mode) if self.state else 0
        wants_reports = self.settings.enable_report_export if export_reports is None else export_reports

        try:
            targets = self.target_workspaces(incremental, workspace_ids)
        except ApiError as exc:
            report.errors.append(f"could not list workspaces: {exc}")
            report.finished_at = datetime.now(timezone.utc)
            if self.state:
                self.state.finish_run(run_id, 0, "failed", str(exc))
            return report

        report.workspaces_requested = len(targets)
        if not targets:
            report.finished_at = datetime.now(timezone.utc)
            if self.state:
                self.state.finish_run(run_id, 0, "succeeded", "nothing to scan")
            report.graph_stats = self.store.stats()
            return report

        capacity_skus = self.admin.get_capacity_skus()
        kwargs = {"sleep": sleep} if sleep is not None else {}
        try:
            raw_results = self.admin.scan_workspaces(targets, **kwargs)
        except ApiError as exc:
            report.errors.append(f"scan failed: {exc}")
            report.finished_at = datetime.now(timezone.utc)
            if self.state:
                self.state.finish_run(run_id, 0, "failed", str(exc))
            return report

        snapshot = snapshot_from_scan_results(raw_results, capacity_skus)
        report.warnings.extend(snapshot.warnings)

        router = self._build_router(snapshot, capacity_skus)
        dependencies = self._resolve_dependencies(snapshot, router, report)
        if self.settings.enable_dataflows:
            self._collect_dataflows(snapshot, report)
        if wants_reports:
            self._collect_report_layouts(snapshot, report, sleep=sleep)

        graph = GraphBuilder().build(snapshot, dependencies)
        self._persist(snapshot, graph, incremental, raw_results)

        report.workspaces_scanned = len(snapshot.workspaces)
        report.datasets = sum(len(w.datasets) for w in snapshot.workspaces)
        report.reports = sum(len(w.reports) for w in snapshot.workspaces)
        report.reports_with_layout = sum(
            1 for w in snapshot.workspaces for r in w.reports if r.layout_available
        )
        report.dataflows = sum(len(w.dataflows) for w in snapshot.workspaces)
        report.routing = router.summary()
        report.warnings.extend(graph.warnings)
        report.graph_stats = self.store.stats()
        report.finished_at = datetime.now(timezone.utc)

        if self.state:
            self.state.finish_run(run_id, report.workspaces_scanned, "succeeded")
        return report

    # -- steps ------------------------------------------------------------
    def _build_router(self, snapshot: TenantSnapshot, capacity_skus: dict[str, str]) -> CapacityRouter:
        router = CapacityRouter(capacity_skus=capacity_skus)
        if self.xmla is not None and self.xmla.available:
            self.xmla.workspace_names.update({w.id: w.name for w in snapshot.workspaces})
            router.xmla_resolver = XmlaDependencyResolver(self.xmla)
        return router

    def _resolve_dependencies(
        self, snapshot: TenantSnapshot, router: CapacityRouter, report: ScanReport
    ) -> dict[str, DependencyResult]:
        results: dict[str, DependencyResult] = {}
        for workspace in snapshot.workspaces:
            for index, dataset in enumerate(workspace.datasets):
                enriched = self._enrich_schema(workspace, dataset, report)
                workspace.datasets[index] = enriched
                result = router.resolve(workspace, enriched)
                results[enriched.id] = result
                report.warnings.extend(result.warnings)
        return results

    def _enrich_schema(
        self, workspace: WorkspaceSpec, dataset: DatasetSpec, report: ScanReport
    ) -> DatasetSpec:
        """Prefer the TMSCHEMA DMVs over the Scanner API's schema where possible.

        The DMVs know a partition's type, which the Scanner API does not, and
        that is what tells a DAX calculated table from an M query.
        """
        if self.xmla is None or not self.xmla.available or not workspace.tier.has_xmla:
            return dataset
        try:
            enriched = self.xmla.fetch_schema(dataset)
        except XmlaUnavailable as exc:
            report.warnings.append(
                f"model '{dataset.name}': XMLA schema unavailable ({exc}); " "using the Scanner API schema"
            )
            return dataset
        if not enriched.tables:
            return dataset
        # Keep the data-source usages the Scanner API gave us; the DMVs have none.
        return enriched.model_copy(update={"data_sources": dataset.data_sources})

    def _collect_dataflows(self, snapshot: TenantSnapshot, report: ScanReport) -> None:
        for workspace in snapshot.workspaces:
            for dataflow in workspace.dataflows:
                model_json = self.admin.export_dataflow(dataflow.id)
                if not model_json:
                    report.warnings.append(
                        f"dataflow '{dataflow.name}' could not be exported; its entities are "
                        "in the graph but their M lineage is not"
                    )
                    continue
                dataflow.queries = dataflow_queries_from_model_json(model_json)

    def _collect_report_layouts(self, snapshot: TenantSnapshot, report: ScanReport, sleep=None) -> None:
        kwargs = {"sleep": sleep} if sleep is not None else {}
        with tempfile.TemporaryDirectory(prefix="pbilineage-export-") as workdir:
            for workspace in snapshot.workspaces:
                for report_spec in workspace.reports:
                    self._export_one_report(workspace, report_spec, Path(workdir), report, **kwargs)

    def _export_one_report(
        self,
        workspace: WorkspaceSpec,
        report_spec: ReportSpec,
        workdir: Path,
        report: ScanReport,
        **kwargs,
    ) -> None:
        destination = workdir / f"{report_spec.id}.pbix"
        try:
            path = self.admin.export_report(workspace.id, report_spec.id, destination, **kwargs)
        except ApiError as exc:
            report.warnings.append(f"report '{report_spec.name}' export failed: {exc}")
            return
        if path is None:
            report.warnings.append(
                f"report '{report_spec.name}' was not exportable (live connection, protection "
                "policy, or export disabled); model lineage is still present"
            )
            return
        try:
            report_spec.pages = parse_pbix_layout(path)
            report_spec.layout_available = bool(report_spec.pages)
        except Exception as exc:  # noqa: BLE001 - a malformed PBIX is not fatal
            report.warnings.append(f"report '{report_spec.name}' layout could not be parsed: {exc}")
        finally:
            path.unlink(missing_ok=True)

    def _persist(
        self,
        snapshot: TenantSnapshot,
        graph: LineageGraph,
        incremental: bool,
        raw_results: list[dict[str, Any]],
    ) -> None:
        if incremental:
            # Replace each scanned workspace wholesale so deletions propagate.
            for workspace in snapshot.workspaces:
                per_workspace = LineageGraph(
                    nodes={
                        node_id: node
                        for node_id, node in graph.nodes.items()
                        if node.workspace_id in (workspace.id, None)
                    },
                    scanned_at=graph.scanned_at,
                )
                ids = set(per_workspace.nodes)
                per_workspace.edges = [
                    edge for edge in graph.edges if edge.source in ids and edge.target in ids
                ]
                self.store.replace_workspace(workspace.id, per_workspace)
            self.store.graph.warnings = list(graph.warnings)
        else:
            self.store.write(graph)

        if self.state is None:
            return
        payloads = _workspace_payloads(raw_results)
        for workspace in snapshot.workspaces:
            self.state.record(
                workspace.id,
                scanned_at=snapshot.scanned_at,
                payload_hash=content_hash(payloads.get(workspace.id, {})),
            )


def _workspace_payloads(raw_results: list[dict[str, Any]]) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for result in raw_results:
        for workspace in result.get("workspaces") or []:
            if isinstance(workspace, dict) and workspace.get("id"):
                payloads[str(workspace["id"])] = workspace
    return payloads
