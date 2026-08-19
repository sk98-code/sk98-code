"""Thin report engine — build spec §5.4, Milestone 7b.

A shared model's usage is the **union** across every connected consumer,
and those consumers live in workspaces you have not looked at. This module
builds the dataset→consumers index, retrieves and parses each one,
records *why* each failure happened, and enforces the completeness gate:

> A shared model's objects may only be reported as `Unused` if every
> single connected report was successfully retrieved and parsed.

The gate is code, not UI copy: `ModelCoverage.complete` feeds
`analyze_model(coverage_complete=…)`, which caps every would-be-Unused
verdict, which in turn blocks the removal script generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pbi_lineage.readers.pbix import read_pbix_bytes
from pbi_lineage.readers.rdl import rdl_references, read_rdl
from pbi_lineage.schema import FieldReference, Model, ReportLayout

# ---------------------------------------------------------------------------
# Consumer inventory
# ---------------------------------------------------------------------------


class ConsumerType(str, Enum):
    THIN_REPORT = "thin_report"
    PAGINATED = "paginated"
    EXCEL_AIE = "excel_analyze_in_excel"
    COMPOSITE_MODEL = "composite_model"
    NOTEBOOK = "notebook"
    DATAFLOW = "dataflow"
    API = "api"  # executeQueries / embedded — unknowable (§14)


# Consumers whose content can never be parsed: their presence alone caps
# confidence for the whole model (§5.4.5).
_UNPARSEABLE = {ConsumerType.EXCEL_AIE, ConsumerType.API}

# Auto-generated reports that are safe to skip entirely (§5.4.3).
_SKIPPABLE_NAMES = ("usage metrics report", "report usage metrics report")


class FailureCause(str, Enum):
    SENSITIVITY_LABEL = "sensitivity_label_encrypted"
    UNSUPPORTED_REPORT = "unsupported_report_type"
    PERMISSION = "permission_denied"
    NOT_FOUND = "not_found"
    THROTTLED = "throttled"
    PARSE_ERROR = "parse_error"
    OTHER = "other"


_STATUS_TO_CAUSE = {
    401: FailureCause.PERMISSION,
    403: FailureCause.PERMISSION,
    404: FailureCause.NOT_FOUND,
    429: FailureCause.THROTTLED,
}


@dataclass
class Consumer:
    id: str
    name: str
    workspace_id: str
    consumer_type: ConsumerType
    dataset_id: str | None = None
    workspace_name: str = ""
    is_app_copy: bool = False

    @property
    def skippable(self) -> bool:
        return self.name.strip().lower() in _SKIPPABLE_NAMES


@dataclass
class RetrievalFailure:
    consumer: Consumer
    cause: FailureCause
    detail: str = ""


@dataclass
class ModelCoverage:
    """Per-model retrieval accounting — the completeness gate's evidence."""

    dataset_id: str
    retrievable: list[Consumer] = field(default_factory=list)
    parsed: list[Consumer] = field(default_factory=list)
    failures: list[RetrievalFailure] = field(default_factory=list)
    skipped: list[Consumer] = field(default_factory=list)
    unparseable_consumers: list[Consumer] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failures and len(self.parsed) == len(self.retrievable)

    @property
    def external_consumer_labels(self) -> list[str]:
        return [f"{c.consumer_type.value}: {c.name}" for c in self.unparseable_consumers]

    def summary(self) -> str:
        """The §5.4.4 UI line: never a silent percentage."""
        line = f"{len(self.parsed)} of {len(self.retrievable)} connected reports analyzed"
        if self.failures:
            line += f" — {len(self.failures)} could not be retrieved"
        return line

    def failure_report(self) -> list[dict]:
        return [
            {
                "report_id": failure.consumer.id,
                "report_name": failure.consumer.name,
                "workspace_id": failure.consumer.workspace_id,
                "type": failure.consumer.consumer_type.value,
                "cause": failure.cause.value,
                "detail": failure.detail,
            }
            for failure in self.failures
        ]


# ---------------------------------------------------------------------------
# Index building (§5.4.1)
# ---------------------------------------------------------------------------


def build_consumer_index(scan_result: dict) -> dict[str, list[Consumer]]:
    """Scanner payload → `dataset_id -> [Consumer]`, built once per scan.

    Covers reports (thin and thick), paginated reports, composite models
    chained on another model, dataflows and notebooks with lineage back to
    a dataset.
    """
    index: dict[str, list[Consumer]] = {}

    def add(dataset_id: str | None, consumer: Consumer) -> None:
        if not dataset_id:
            return
        index.setdefault(dataset_id, []).append(consumer)

    for workspace in scan_result.get("workspaces", []) or []:
        workspace_id = str(workspace.get("id", ""))
        workspace_name = workspace.get("name", "")
        for report in workspace.get("reports", []) or []:
            dataset_id = report.get("datasetId")
            # A thin report is one whose model lives elsewhere (§5.4.1).
            dataset_workspace = report.get("datasetWorkspaceId")
            consumer_type = (
                ConsumerType.PAGINATED
                if (report.get("reportType") or "").lower() == "paginatedreport"
                else ConsumerType.THIN_REPORT
            )
            add(
                dataset_id,
                Consumer(
                    id=str(report.get("id", "")),
                    name=report.get("name", ""),
                    workspace_id=workspace_id,
                    workspace_name=workspace_name,
                    consumer_type=consumer_type,
                    dataset_id=dataset_id,
                    is_app_copy=bool(report.get("appId")),
                ),
            )
            if dataset_workspace and dataset_workspace != workspace_id:
                pass  # cross-workspace thin report: already indexed by dataset id
        for dataset in workspace.get("datasets", []) or []:
            # composite / DirectQuery-over-PBI: this model consumes another
            for upstream in dataset.get("upstreamDatasets", []) or []:
                add(
                    str(upstream.get("targetDatasetId", "")),
                    Consumer(
                        id=str(dataset.get("id", "")),
                        name=dataset.get("name", ""),
                        workspace_id=workspace_id,
                        workspace_name=workspace_name,
                        consumer_type=ConsumerType.COMPOSITE_MODEL,
                        dataset_id=str(upstream.get("targetDatasetId", "")),
                    ),
                )
        for notebook in workspace.get("notebooks", []) or []:
            for upstream in notebook.get("upstreamDatasets", []) or []:
                add(
                    str(upstream.get("targetDatasetId", "")),
                    Consumer(
                        id=str(notebook.get("id", "")),
                        name=notebook.get("name", ""),
                        workspace_id=workspace_id,
                        workspace_name=workspace_name,
                        consumer_type=ConsumerType.NOTEBOOK,
                        dataset_id=str(upstream.get("targetDatasetId", "")),
                    ),
                )
    return index


def detect_excel_consumers(activity_events: list[dict], dataset_id: str) -> list[Consumer]:
    """Analyze-in-Excel connections from the Activity log (§5.4.5).

    An AiE workbook lives outside the tenant and can never be parsed, so
    its presence alone reduces confidence for the whole model.
    """
    consumers: list[Consumer] = []
    seen: set[str] = set()
    for event in activity_events:
        if event.get("Activity") not in ("AnalyzedByExternalApplication", "AnalyzeInExcel"):
            continue
        if str(event.get("DatasetId", "")) != dataset_id:
            continue
        user = str(event.get("UserId", "unknown"))
        if user in seen:
            continue
        seen.add(user)
        consumers.append(
            Consumer(
                id=f"aie:{user}",
                name=f"Analyze in Excel ({user})",
                workspace_id=str(event.get("WorkspaceId", "")),
                consumer_type=ConsumerType.EXCEL_AIE,
                dataset_id=dataset_id,
            )
        )
    return consumers


# ---------------------------------------------------------------------------
# Retrieval + parsing (§5.4.3)
# ---------------------------------------------------------------------------


class ReportFetcher:
    """Downloads report content. `export_pbix` / `export_rdl` return
    `(bytes, None)` on success or `(None, RetrievalFailure)` on failure."""

    def __init__(self, client):
        self.client = client

    def _fetch(self, path: str, consumer: Consumer):
        response = self.client.get(path)
        if response.ok:
            return response.content, None
        cause = _STATUS_TO_CAUSE.get(response.status, FailureCause.OTHER)
        detail = f"HTTP {response.status}"
        body = response.body if isinstance(response.body, dict) else {}
        message = str(body.get("error", {}).get("message", "")) if body else ""
        if message:
            detail += f": {message}"
            lowered = message.lower()
            if "sensitivity" in lowered or "protected" in lowered or "encrypt" in lowered:
                cause = FailureCause.SENSITIVITY_LABEL
            elif "not supported" in lowered or "unsupported" in lowered:
                cause = FailureCause.UNSUPPORTED_REPORT
        return None, RetrievalFailure(consumer=consumer, cause=cause, detail=detail)

    def export_pbix(self, consumer: Consumer):
        return self._fetch(f"groups/{consumer.workspace_id}/reports/{consumer.id}/Export", consumer)

    def export_rdl(self, consumer: Consumer):
        return self._fetch(f"groups/{consumer.workspace_id}/reports/{consumer.id}/Export", consumer)


def gather_model_usage(
    dataset_id: str,
    consumers: list[Consumer],
    fetcher: ReportFetcher,
    model: Model,
) -> tuple[list[ReportLayout], list[FieldReference], ModelCoverage]:
    """Retrieve and parse every consumer of one model.

    Returns the parsed report layouts, the extra references contributed by
    non-PBIX consumers (paginated queries), and the coverage record whose
    `complete` flag gates every Unused verdict downstream.
    """
    coverage = ModelCoverage(dataset_id=dataset_id)
    layouts: list[ReportLayout] = []
    extra_references: list[FieldReference] = []

    for consumer in consumers:
        if consumer.skippable:
            coverage.skipped.append(consumer)
            continue
        if consumer.consumer_type in _UNPARSEABLE:
            coverage.unparseable_consumers.append(consumer)
            continue
        if consumer.consumer_type in (ConsumerType.DATAFLOW,):
            coverage.skipped.append(consumer)  # lineage node, not a field consumer
            continue

        coverage.retrievable.append(consumer)

        if consumer.consumer_type is ConsumerType.PAGINATED:
            content, failure = fetcher.export_rdl(consumer)
            if failure is not None:
                coverage.failures.append(failure)
                continue
            rdl = read_rdl(content, name=consumer.name)
            if rdl.warnings:
                coverage.failures.append(
                    RetrievalFailure(consumer, FailureCause.PARSE_ERROR, "; ".join(rdl.warnings))
                )
                continue
            extra_references.extend(rdl_references(rdl, model))
            coverage.parsed.append(consumer)
            continue

        if consumer.consumer_type is ConsumerType.COMPOSITE_MODEL:
            # A downstream composite model is itself a consumer, and *its*
            # thin reports are transitive consumers (§5.4.5). Its own field
            # usage is only knowable by analyzing that model in turn, so
            # until that recursion runs it caps confidence.
            coverage.unparseable_consumers.append(consumer)
            coverage.retrievable.remove(consumer)
            continue

        if consumer.consumer_type is ConsumerType.NOTEBOOK:
            coverage.unparseable_consumers.append(consumer)
            coverage.retrievable.remove(consumer)
            continue

        content, failure = fetcher.export_pbix(consumer)
        if failure is not None:
            coverage.failures.append(failure)
            continue
        try:
            result = read_pbix_bytes(content, name=consumer.name)
        except Exception as exc:  # noqa: BLE001 — a corrupt export must not abort the scan
            coverage.failures.append(RetrievalFailure(consumer, FailureCause.PARSE_ERROR, str(exc)))
            continue
        if result.report is None:
            coverage.failures.append(
                RetrievalFailure(consumer, FailureCause.PARSE_ERROR, "; ".join(result.warnings))
            )
            continue
        layouts.append(result.report)
        coverage.parsed.append(consumer)

    return layouts, extra_references, coverage


def attach_references(layout: ReportLayout, references: list[FieldReference]) -> ReportLayout:
    """Fold non-visual consumer references (paginated queries) into a
    synthetic report layout so the resolver treats them as roots."""
    layout.filters.extend(references)
    return layout


def synthetic_layout(name: str, references: list[FieldReference]) -> ReportLayout:
    return ReportLayout(name=name, source_format="synthetic", filters=list(references))


# ---------------------------------------------------------------------------
# Thin-report analyses that fall out for free (§5.4.7)
# ---------------------------------------------------------------------------


def _reference_multiset(layout: ReportLayout) -> set[tuple]:
    return {(r.table, r.name, r.kind.value) for r in layout.all_references()}


def jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def similar_reports(layouts: list[ReportLayout], *, threshold: float = 0.8) -> list[tuple[str, str, float]]:
    """Report similarity over the field-reference multiset — finds the
    'everyone forked the same report' problem (§5.4.7)."""
    pairs: list[tuple[str, str, float]] = []
    signatures = [
        (layout.name or f"report{i}", _reference_multiset(layout)) for i, layout in enumerate(layouts)
    ]
    for i in range(len(signatures)):
        for j in range(i + 1, len(signatures)):
            score = jaccard(signatures[i][1], signatures[j][1])
            if score >= threshold:
                pairs.append((signatures[i][0], signatures[j][0], round(score, 4)))
    return sorted(pairs, key=lambda p: -p[2])


def field_usage_frequency(layouts: list[ReportLayout]) -> dict[tuple[str | None, str], int]:
    """How many reports reference each field — a column used in 1 of 400
    visuals is a consolidation candidate even though it is 'used'."""
    counts: dict[tuple[str | None, str], int] = {}
    for layout in layouts:
        for table, name, _kind in {(r.table, r.name, r.kind) for r in layout.all_references()}:
            counts[(table, name)] = counts.get((table, name), 0) + 1
    return counts


def orphaned_reports(index: dict[str, list[Consumer]], known_dataset_ids: set[str]) -> list[Consumer]:
    """Reports whose dataset no longer exists (§5.4.7)."""
    orphans: list[Consumer] = []
    for dataset_id, consumers in index.items():
        if dataset_id not in known_dataset_ids:
            orphans.extend(consumers)
    return orphans
