"""Headless / scheduled run mode — build spec Milestone 11.

Runs the whole pipeline without a UI and writes results where a governance
Power BI report can consume them: Delta tables in a Fabric lakehouse when
Spark is present, otherwise JSON + SQLite on local disk. The analysis core
is unchanged — this module only wires sources to sinks and is the entry
point a scheduled Fabric notebook calls:

    from pbi_lineage.notebook import run_tenant_scan
    run_tenant_scan(client, out_dir="/lakehouse/default/Files/pbi_lineage",
                    delta_path="/lakehouse/default/Tables")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.persist import ScanBundle, to_rows, write_json, write_sqlite
from pbi_lineage.resolve import analyze_model
from pbi_lineage.rules import EstateContext, run_rules
from pbi_lineage.scan import scan_tenant
from pbi_lineage.service.scanner import ScannerClient


@dataclass
class RunOutput:
    bundles: list[ScanBundle] = field(default_factory=list)
    json_path: Path | None = None
    sqlite_path: Path | None = None
    delta_tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        from pbi_lineage.resolve import STATUS_UNUSED

        lines = [f"{len(self.bundles)} model(s) analyzed"]
        for bundle in self.bundles:
            unused = len(bundle.analysis.by_status(STATUS_UNUSED)) if bundle.analysis is not None else 0
            coverage = (bundle.coverage or {}).get("summary", "coverage unknown")
            lines.append(
                f"  {bundle.model.name}: {unused} unused object(s), "
                f"{len(bundle.findings)} finding(s) — {coverage}"
            )
        return "\n".join(lines)


def write_delta(rows: dict[str, list[dict]], delta_path: str, *, spark: Any = None) -> list[str]:
    """Write the row export as Delta tables. Requires a Spark session —
    supplied by the Fabric notebook runtime, or passed in for tests."""
    if spark is None:  # pragma: no cover - Fabric runtime only
        try:
            from pyspark.sql import SparkSession  # noqa: PLC0415 — optional

            spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
        except ImportError as exc:
            raise RuntimeError(
                "Delta output needs a Spark session (Fabric notebook) — "
                "use JSON/SQLite output outside Fabric"
            ) from exc
    written: list[str] = []
    for name, table_rows in rows.items():
        if not table_rows:
            continue
        target = f"{delta_path.rstrip('/')}/{name}"
        spark.createDataFrame(table_rows).write.format("delta").mode("overwrite").save(target)
        written.append(target)
    return written


def run_tenant_scan(
    client,
    *,
    out_dir: str | Path,
    workspace_ids: list[str] | None = None,
    modified_since: str | None = None,
    delta_path: str | None = None,
    spark: Any = None,
    activity_events: list[dict] | None = None,
) -> RunOutput:
    """Full headless pipeline: scan → analyze → rules → persist."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = RunOutput()

    scanner = ScannerClient(client, out_dir / "scan")
    ok, message = scanner.preflight()
    if not ok:
        output.warnings.append(message)
    ids = workspace_ids or scanner.list_workspace_ids(modified_since=modified_since)
    outcome = scanner.scan(ids, modified_since=modified_since)
    output.warnings.extend(outcome.warnings)
    if not outcome.complete:
        output.warnings.append("tenant scan incomplete — some workspace batches failed; verdicts stay capped")

    output.bundles = scan_tenant(
        scanner.merged_result(),
        client=client,
        activity_events=activity_events or [],
    )

    output.json_path = write_json(out_dir / "pbi_lineage_export.json", output.bundles)
    output.sqlite_path = write_sqlite(out_dir / "pbi_lineage.sqlite", output.bundles)
    if delta_path:
        output.delta_tables = write_delta(to_rows(output.bundles), delta_path, spark=spark)
    return output


def analyze_local_file(path: str | Path, *, out_dir: str | Path | None = None) -> RunOutput:
    """Single-file mode (M1): analyze a PBIX/PBIP and optionally persist."""
    from pbi_lineage.readers import read_any

    result = read_any(path)
    output = RunOutput(warnings=list(result.warnings))
    if result.model is None:
        output.warnings.append(
            f"{path}: no model metadata available (thin report or ABF DataModel) — "
            "report layer parsed only"
        )
        return output

    reports = [result.report] if result.report is not None else []
    analysis = analyze_model(result.model, reports)
    m_index = MExpressionIndex()
    m_index.add_model(result.model)
    findings = run_rules(
        EstateContext(model=result.model, analysis=analysis, reports=reports, m_index=m_index)
    )
    bundle = ScanBundle(
        model=result.model,
        analysis=analysis,
        reports=reports,
        findings=findings,
        coverage={"summary": "single local file — external consumers unknown"},
    )
    output.bundles = [bundle]
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        output.json_path = write_json(out_dir / "pbi_lineage_export.json", [bundle])
        output.sqlite_path = write_sqlite(out_dir / "pbi_lineage.sqlite", [bundle])
    return output
