"""Persistence and export — build spec Section 8 schema, Milestone 10 (C10).

SQLite for a single scan, JSON for sharing and re-import, and a
column-oriented row export (Parquet/Delta-ready dicts) for tenant scans
that land in a Fabric lakehouse.

`evidence_json` is not optional in this schema: every reference row
records where it was found, so the UI can justify a *delete this*
recommendation.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pbi_lineage.resolve import AnalysisResult
from pbi_lineage.rules import Finding
from pbi_lineage.schema import Model, ReportLayout
from pbi_lineage.size import SizeReport

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS scan(scan_id TEXT PRIMARY KEY, tenant_id TEXT, scanned_at TEXT,
    schema_version INTEGER, tool_version TEXT);
CREATE TABLE IF NOT EXISTS workspace(workspace_id TEXT PRIMARY KEY, name TEXT, type TEXT,
    capacity_id TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS model(model_id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
    storage_mode TEXT, size_bytes INTEGER, created TEXT, last_refresh TEXT);
CREATE TABLE IF NOT EXISTS "table"(table_id TEXT PRIMARY KEY, model_id TEXT, name TEXT,
    is_hidden INTEGER, is_calculated INTEGER, row_count INTEGER, size_bytes INTEGER);
CREATE TABLE IF NOT EXISTS column_(column_id TEXT PRIMARY KEY, table_id TEXT, name TEXT,
    data_type TEXT, is_hidden INTEGER, is_calculated INTEGER, is_key INTEGER,
    sort_by_column_id TEXT, dictionary_bytes INTEGER, data_bytes INTEGER,
    hierarchy_bytes INTEGER, expression TEXT);
CREATE TABLE IF NOT EXISTS measure(measure_id TEXT PRIMARY KEY, table_id TEXT, name TEXT,
    expression TEXT, format_string TEXT, is_hidden INTEGER, display_folder TEXT,
    is_report_level INTEGER, host_report_id TEXT);
CREATE TABLE IF NOT EXISTS relationship(rel_id TEXT PRIMARY KEY, model_id TEXT,
    from_column_id TEXT, to_column_id TEXT, is_active INTEGER, cardinality TEXT);
CREATE TABLE IF NOT EXISTS report(report_id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
    model_id TEXT, report_type TEXT);
CREATE TABLE IF NOT EXISTS page(page_id TEXT PRIMARY KEY, report_id TEXT, name TEXT,
    display_name TEXT, is_hidden INTEGER, ordinal INTEGER);
CREATE TABLE IF NOT EXISTS visual(visual_id TEXT PRIMARY KEY, page_id TEXT, visual_type TEXT,
    title TEXT, x REAL, y REAL, w REAL, h REAL, is_hidden INTEGER);
CREATE TABLE IF NOT EXISTS reference(ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id TEXT, target_node_id TEXT, edge_kind TEXT, confidence TEXT,
    evidence_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(object_id TEXT PRIMARY KEY, status TEXT, reason TEXT,
    reclaimable_bytes INTEGER);
CREATE TABLE IF NOT EXISTS m_expression(expr_id TEXT PRIMARY KEY, model_id TEXT,
    item_name TEXT, m_code TEXT);
CREATE TABLE IF NOT EXISTS finding(finding_id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT,
    severity TEXT, object_id TEXT, message TEXT, detail_json TEXT);
CREATE INDEX IF NOT EXISTS ix_reference_target ON reference(target_node_id);
CREATE INDEX IF NOT EXISTS ix_reference_source ON reference(source_node_id);
CREATE INDEX IF NOT EXISTS ix_usage_status ON usage(status);
"""


@dataclass
class ScanBundle:
    """One analyzed model plus everything derived from it."""

    model: Model
    analysis: AnalysisResult | None = None
    reports: list[ReportLayout] = field(default_factory=list)
    size: SizeReport | None = None
    findings: list[Finding] = field(default_factory=list)
    workspace_id: str | None = None
    workspace_name: str | None = None
    model_id: str | None = None
    coverage: dict | None = None

    @property
    def identifier(self) -> str:
        return self.model_id or self.model.name


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _table_id(bundle: ScanBundle, table: str) -> str:
    return f"{bundle.identifier}|{table}"


def write_sqlite(path: str | Path, bundles: Iterable[ScanBundle], *, tenant_id: str = "") -> Path:
    path = Path(path)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_DDL)
        from datetime import datetime, timezone

        connection.execute(
            "INSERT OR REPLACE INTO scan VALUES (?,?,?,?,?)",
            (
                "scan",
                tenant_id,
                datetime.now(timezone.utc).isoformat(),
                SCHEMA_VERSION,
                _tool_version(),
            ),
        )
        for bundle in bundles:
            _write_bundle(connection, bundle)
        connection.commit()
    finally:
        connection.close()
    return path


def _tool_version() -> str:
    from pbi_lineage import __version__

    return __version__


def _write_bundle(connection: sqlite3.Connection, bundle: ScanBundle) -> None:
    model = bundle.model
    model_id = bundle.identifier
    if bundle.workspace_id:
        connection.execute(
            "INSERT OR REPLACE INTO workspace VALUES (?,?,?,?,?)",
            (bundle.workspace_id, bundle.workspace_name or "", "Workspace", None, "Active"),
        )
    total_bytes = sum(t.total_bytes for t in bundle.size.tables.values()) if bundle.size is not None else None
    connection.execute(
        "INSERT OR REPLACE INTO model VALUES (?,?,?,?,?,?,?)",
        (model_id, bundle.workspace_id, model.name, None, total_bytes, None, None),
    )

    for table in model.tables:
        table_id = _table_id(bundle, table.name)
        table_size = bundle.size.tables.get(table.name) if bundle.size else None
        connection.execute(
            'INSERT OR REPLACE INTO "table" VALUES (?,?,?,?,?,?,?)',
            (
                table_id,
                model_id,
                table.name,
                int(table.is_hidden),
                int(table.is_calculated),
                table_size.row_count if table_size else None,
                table_size.total_bytes if table_size else None,
            ),
        )
        for column in table.columns:
            size = bundle.size.column(table.name, column.name) if bundle.size else None
            connection.execute(
                "INSERT OR REPLACE INTO column_ VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{table_id}[{column.name}]",
                    table_id,
                    column.name,
                    column.data_type,
                    int(column.is_hidden),
                    int(column.is_calculated),
                    int(column.is_key),
                    f"{table_id}[{column.sort_by_column}]" if column.sort_by_column else None,
                    size.dictionary_bytes if size else None,
                    size.data_bytes if size else None,
                    (size.attribute_hierarchy_bytes + size.user_hierarchy_share_bytes) if size else None,
                    column.expression,
                ),
            )
        for measure in table.measures:
            connection.execute(
                "INSERT OR REPLACE INTO measure VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"{model_id}|measure|{measure.name}",
                    table_id,
                    measure.name,
                    measure.expression,
                    measure.format_string,
                    int(measure.is_hidden),
                    measure.display_folder,
                    int(measure.is_report_level),
                    measure.host_report,
                ),
            )
    for relationship in model.relationships:
        connection.execute(
            "INSERT OR REPLACE INTO relationship VALUES (?,?,?,?,?,?)",
            (
                f"{model_id}|rel|{relationship.name}",
                model_id,
                f"{_table_id(bundle, relationship.from_table)}[{relationship.from_column}]",
                f"{_table_id(bundle, relationship.to_table)}[{relationship.to_column}]",
                int(relationship.is_active),
                relationship.to_cardinality,
            ),
        )
    for expression in model.expressions:
        connection.execute(
            "INSERT OR REPLACE INTO m_expression VALUES (?,?,?,?)",
            (f"{model_id}|expr|{expression.name}", model_id, expression.name, expression.expression),
        )

    for report in bundle.reports:
        report_id = f"{model_id}|report|{report.name}"
        connection.execute(
            "INSERT OR REPLACE INTO report VALUES (?,?,?,?,?)",
            (report_id, bundle.workspace_id, report.name, model_id, report.source_format),
        )
        for page in report.pages:
            page_id = f"{report_id}|{page.name}"
            connection.execute(
                "INSERT OR REPLACE INTO page VALUES (?,?,?,?,?,?)",
                (page_id, report_id, page.name, page.display_name, int(page.is_hidden), page.ordinal),
            )
            for visual in page.visuals:
                connection.execute(
                    "INSERT OR REPLACE INTO visual VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        f"{page_id}|{visual.name}",
                        page_id,
                        visual.visual_type,
                        None,
                        visual.x,
                        visual.y,
                        visual.width,
                        visual.height,
                        int(visual.is_hidden),
                    ),
                )

    if bundle.analysis is not None:
        for edge in bundle.analysis.graph.edges:
            connection.execute(
                "INSERT INTO reference(source_node_id,target_node_id,edge_kind,confidence,evidence_json)"
                " VALUES (?,?,?,?,?)",
                (
                    edge.source,
                    edge.target,
                    edge.kind,
                    edge.confidence,
                    json.dumps({"evidence": edge.evidence}),
                ),
            )
        for object_id, verdict in bundle.analysis.verdicts.items():
            reclaimable = 0
            if bundle.size is not None and verdict.kind == "column" and verdict.table:
                size = bundle.size.column(verdict.table, verdict.name)
                reclaimable = size.reclaimable_low if size else 0
            connection.execute(
                "INSERT OR REPLACE INTO usage VALUES (?,?,?,?)",
                (object_id, verdict.status, "; ".join(verdict.reasons), reclaimable),
            )
    for finding in bundle.findings:
        connection.execute(
            "INSERT INTO finding(rule_id,severity,object_id,message,detail_json) VALUES (?,?,?,?,?)",
            (
                finding.rule_id,
                finding.severity,
                finding.object_id,
                finding.message,
                json.dumps(finding.detail),
            ),
        )


# ---------------------------------------------------------------------------
# JSON export / re-import (C10: share results, load them back)
# ---------------------------------------------------------------------------


def to_dict(bundle: ScanBundle) -> dict:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": _tool_version(),
        "model": {
            "id": bundle.identifier,
            "name": bundle.model.name,
            "workspace_id": bundle.workspace_id,
            "tables": [
                {
                    "name": table.name,
                    "is_hidden": table.is_hidden,
                    "columns": [
                        {
                            "name": column.name,
                            "data_type": column.data_type,
                            "is_hidden": column.is_hidden,
                            "is_calculated": column.is_calculated,
                            "expression": column.expression,
                            "sort_by_column": column.sort_by_column,
                        }
                        for column in table.columns
                    ],
                    "measures": [
                        {
                            "name": measure.name,
                            "expression": measure.expression,
                            "is_hidden": measure.is_hidden,
                            "display_folder": measure.display_folder,
                            "is_report_level": measure.is_report_level,
                            "host_report": measure.host_report,
                        }
                        for measure in table.measures
                    ],
                }
                for table in bundle.model.tables
            ],
        },
        "coverage": bundle.coverage,
        "findings": [
            {
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "object_id": finding.object_id,
                "message": finding.message,
                "detail": finding.detail,
            }
            for finding in bundle.findings
        ],
    }
    if bundle.analysis is not None:
        payload["verdicts"] = [
            {
                "object_id": verdict.object_id,
                "kind": verdict.kind,
                "table": verdict.table,
                "name": verdict.name,
                "status": verdict.status,
                "is_hidden": verdict.is_hidden,
                "reasons": verdict.reasons,
                "evidence": verdict.evidence,
            }
            for verdict in bundle.analysis.verdicts.values()
        ]
        payload["edges"] = [
            {
                "source": edge.source,
                "target": edge.target,
                "kind": edge.kind,
                "confidence": edge.confidence,
                "evidence": edge.evidence,
            }
            for edge in bundle.analysis.graph.edges
        ]
    if bundle.size is not None:
        payload["sizes"] = [
            {
                "table": table_size.table,
                "row_count": table_size.row_count,
                "columns": [
                    {
                        "column": column.column,
                        "data_bytes": column.data_bytes,
                        "dictionary_bytes": column.dictionary_bytes,
                        "attribute_hierarchy_bytes": column.attribute_hierarchy_bytes,
                        "user_hierarchy_share_bytes": column.user_hierarchy_share_bytes,
                        "reclaimable_low": column.reclaimable_low,
                        "reclaimable_high": column.reclaimable_high,
                    }
                    for column in table_size.columns.values()
                ],
            }
            for table_size in bundle.size.tables.values()
        ]
    return payload


def write_json(path: str | Path, bundles: Iterable[ScanBundle]) -> Path:
    path = Path(path)
    path.write_text(
        json.dumps({"bundles": [to_dict(bundle) for bundle in bundles]}, indent=2), encoding="utf-8"
    )
    return path


@dataclass
class ImportedScan:
    """A re-imported export: enough to browse verdicts and findings without
    re-running the analysis (C10)."""

    model_name: str
    workspace_id: str | None
    verdicts: dict[str, dict] = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    coverage: dict | None = None
    schema_version: int = SCHEMA_VERSION

    def by_status(self, status: str) -> list[dict]:
        return [verdict for verdict in self.verdicts.values() if verdict["status"] == status]

    def consumers_of(self, object_id: str) -> list[dict]:
        return [edge for edge in self.edges if edge["target"] == object_id]


def read_json(path: str | Path) -> list[ImportedScan]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    scans: list[ImportedScan] = []
    for bundle in payload.get("bundles", []):
        version = bundle.get("schema_version", 0)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"export schema version {version} is newer than this tool supports ({SCHEMA_VERSION})"
            )
        scans.append(
            ImportedScan(
                model_name=bundle.get("model", {}).get("name", ""),
                workspace_id=bundle.get("model", {}).get("workspace_id"),
                verdicts={v["object_id"]: v for v in bundle.get("verdicts", [])},
                findings=bundle.get("findings", []),
                edges=bundle.get("edges", []),
                coverage=bundle.get("coverage"),
                schema_version=version,
            )
        )
    return scans


# ---------------------------------------------------------------------------
# Row export for Delta / Parquet (tenant scale)
# ---------------------------------------------------------------------------


def to_rows(bundles: Iterable[ScanBundle]) -> dict[str, list[dict]]:
    """Column-oriented rows keyed by table name, ready for
    `spark.createDataFrame` / `polars.DataFrame` / Delta write."""
    rows: dict[str, list[dict]] = {
        "model": [],
        "table": [],
        "column": [],
        "measure": [],
        "usage": [],
        "reference": [],
        "finding": [],
    }
    for bundle in bundles:
        model_id = bundle.identifier
        rows["model"].append(
            {"model_id": model_id, "name": bundle.model.name, "workspace_id": bundle.workspace_id}
        )
        for table in bundle.model.tables:
            rows["table"].append({"model_id": model_id, "table": table.name, "is_hidden": table.is_hidden})
            for column in table.columns:
                size = bundle.size.column(table.name, column.name) if bundle.size else None
                rows["column"].append(
                    {
                        "model_id": model_id,
                        "table": table.name,
                        "column": column.name,
                        "data_type": column.data_type,
                        "is_hidden": column.is_hidden,
                        "data_bytes": size.data_bytes if size else None,
                        "dictionary_bytes": size.dictionary_bytes if size else None,
                    }
                )
            for measure in table.measures:
                rows["measure"].append(
                    {
                        "model_id": model_id,
                        "table": table.name,
                        "measure": measure.name,
                        "expression": measure.expression,
                        "is_report_level": measure.is_report_level,
                    }
                )
        if bundle.analysis is not None:
            for verdict in bundle.analysis.verdicts.values():
                rows["usage"].append(
                    {
                        "model_id": model_id,
                        "object_id": verdict.object_id,
                        "kind": verdict.kind,
                        "table": verdict.table,
                        "name": verdict.name,
                        "status": verdict.status,
                        "reasons": "; ".join(verdict.reasons),
                    }
                )
            for edge in bundle.analysis.graph.edges:
                rows["reference"].append(
                    {
                        "model_id": model_id,
                        "source_node_id": edge.source,
                        "target_node_id": edge.target,
                        "edge_kind": edge.kind,
                        "confidence": edge.confidence,
                        "evidence_json": json.dumps({"evidence": edge.evidence}),
                    }
                )
        for finding in bundle.findings:
            rows["finding"].append(
                {
                    "model_id": model_id,
                    "rule_id": finding.rule_id,
                    "severity": finding.severity,
                    "object_id": finding.object_id,
                    "message": finding.message,
                }
            )
    return rows
