"""Tenant orchestration: Scanner payload → analyzed bundles.

Ties the pieces together in the order the spec requires: build the
dataset→consumers index first, retrieve and parse every consumer of a
model, *then* analyze — so report-level measures and cross-workspace thin
reports are in the graph before any verdict is computed, and the coverage
gate rides along into every verdict.
"""

from __future__ import annotations

from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.persist import ScanBundle
from pbi_lineage.resolve import analyze_model
from pbi_lineage.rules import EstateContext, run_rules
from pbi_lineage.schema import Model
from pbi_lineage.service.scanner import model_from_dataset_schema
from pbi_lineage.service.thin_reports import (
    ReportFetcher,
    build_consumer_index,
    detect_excel_consumers,
    gather_model_usage,
    synthetic_layout,
)
from pbi_lineage.service.xmla import decide_source, workspace_capability_from_scanner


def scan_tenant(
    scan_result: dict,
    *,
    client=None,
    activity_events: list[dict] | None = None,
    model_provider=None,
) -> list[ScanBundle]:
    """Analyze every dataset in a Scanner payload.

    `model_provider(dataset_json, decision) -> Model | None` lets a caller
    supply a full-fidelity model over XMLA where the workspace supports it;
    without one, the Scanner's `datasetSchema` is used and the bundle is
    marked reduced-confidence (mode M4).
    """
    activity_events = activity_events or []
    consumer_index = build_consumer_index(scan_result)
    fetcher = ReportFetcher(client) if client is not None else None
    bundles: list[ScanBundle] = []
    m_index = MExpressionIndex()

    peer_models: list[Model] = []
    for workspace in scan_result.get("workspaces", []) or []:
        for dataset in workspace.get("datasets", []) or []:
            peer_models.append(model_from_dataset_schema(dataset))

    for workspace in scan_result.get("workspaces", []) or []:
        capability = workspace_capability_from_scanner(workspace)
        for dataflow in workspace.get("dataflows", []) or []:
            m_index.add_dataflow(dataflow, workspace_id=capability.workspace_id)

        for dataset in workspace.get("datasets", []) or []:
            dataset_id = str(dataset.get("id", ""))
            decision = decide_source(capability, dataset=dataset.get("name"))
            model = None
            if model_provider is not None:
                model = model_provider(dataset, decision)
            if model is None:
                model = model_from_dataset_schema(dataset)
            m_index.add_model(model, item_id=dataset_id, workspace_id=capability.workspace_id)

            consumers = list(consumer_index.get(dataset_id, []))
            consumers.extend(detect_excel_consumers(activity_events, dataset_id))

            layouts = []
            extra_references = []
            coverage = None
            if fetcher is not None and consumers:
                layouts, extra_references, coverage = gather_model_usage(
                    dataset_id, consumers, fetcher, model
                )
            if extra_references:
                layouts = layouts + [synthetic_layout("non-report consumers", extra_references)]

            coverage_complete = coverage.complete if coverage is not None else False
            external = coverage.external_consumer_labels if coverage is not None else []
            if fetcher is None and consumers:
                external = external or ["report content not retrieved in this run"]

            analysis = analyze_model(
                model,
                layouts,
                coverage_complete=coverage_complete,
                external_consumers=external,
            )
            findings = run_rules(
                EstateContext(
                    model=model,
                    analysis=analysis,
                    reports=layouts,
                    m_index=m_index,
                    peer_models=peer_models,
                )
            )
            coverage_payload = {
                "summary": coverage.summary() if coverage is not None else "no consumers retrieved",
                "complete": coverage_complete,
                "failures": coverage.failure_report() if coverage is not None else [],
                "external_consumers": external,
                "fidelity": decision.fidelity.value,
                "mode": decision.mode.value,
                "notices": decision.notices,
            }
            bundles.append(
                ScanBundle(
                    model=model,
                    analysis=analysis,
                    reports=layouts,
                    findings=findings,
                    workspace_id=capability.workspace_id,
                    workspace_name=capability.name,
                    model_id=dataset_id,
                    coverage=coverage_payload,
                )
            )
    return bundles
