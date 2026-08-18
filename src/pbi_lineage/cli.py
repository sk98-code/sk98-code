"""`pbi-lineage` command line interface.

Also the External Tools entry point: Power BI Desktop launches the tool
with `--server`/`--database` substituted (§4.2), which maps to `live`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from pbi_lineage.connectors import (
    discover_local_instances,
    read_calc_dependencies,
    read_model_via_dmv,
    write_pbitool_manifest,
)
from pbi_lineage.graph import nid_column, nid_measure
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.notebook import analyze_local_file, run_tenant_scan
from pbi_lineage.persist import ScanBundle, write_json, write_sqlite
from pbi_lineage.removal import plan_removal
from pbi_lineage.resolve import STATUS_UNUSED, analyze_model
from pbi_lineage.rules import EstateContext, run_rules
from pbi_lineage.service.rest import HttpxTransport, MsalTokenProvider, RestClient, StaticToken, TokenBucket
from pbi_lineage.size import read_storage_sizes


@click.group()
@click.version_option(package_name="bimigrate")
def main() -> None:
    """Power BI metadata, dependency and lineage analyzer."""


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--out", type=click.Path(), default=None, help="Directory for JSON/SQLite output")
@click.option("--status", default=None, help="Only list objects with this status")
def analyze(path: str, out: str | None, status: str | None) -> None:
    """Analyze a local PBIX file or PBIP project (mode M1)."""
    output = analyze_local_file(path, out_dir=out)
    for warning in output.warnings:
        click.secho(f"warning: {warning}", fg="yellow")
    if not output.bundles:
        sys.exit(1)
    bundle = output.bundles[0]
    click.echo(output.summary())
    if bundle.analysis is not None:
        wanted = status or STATUS_UNUSED
        rows = bundle.analysis.by_status(wanted)
        click.echo(f"\n{wanted} ({len(rows)}):")
        for verdict in sorted(rows, key=lambda v: (v.kind, v.table or "", v.name)):
            click.echo(f"  {verdict.kind:9} {verdict.table or '':20} {verdict.name}")
    if output.json_path:
        click.echo(f"\nexport: {output.json_path}")


@main.command()
@click.option("--server", default=None, help="AS server, e.g. localhost:51423 (Desktop supplies this)")
@click.option("--database", default=None, help="Database name (Desktop supplies this)")
@click.option("--pbix", type=click.Path(exists=True), default=None, help="Report file for the report layer")
@click.option("--out", type=click.Path(), default=None)
def live(server: str | None, database: str | None, pbix: str | None, out: str | None) -> None:
    """Analyze a model open in Power BI Desktop (mode M2)."""
    from pbi_lineage.connectors.local_as import PyadomdExecutor

    if server is None:
        instances = discover_local_instances()
        if not instances:
            raise click.ClickException(
                "no running Power BI Desktop instance found — open a file, or pass --server"
            )
        server = f"localhost:{instances[0].port}"
        click.echo(f"attached to {server}")
    executor = PyadomdExecutor(f"Data Source={server}" + (f";Initial Catalog={database}" if database else ""))

    warnings: list[str] = []
    model = read_model_via_dmv(executor, name=database or "model", warnings=warnings)
    calc_deps = read_calc_dependencies(executor, warnings=warnings)
    size = read_storage_sizes(executor, warnings=warnings)

    reports = []
    if pbix:
        from pbi_lineage.readers import read_any

        read = read_any(pbix)
        if read.report is not None:
            reports.append(read.report)

    analysis = analyze_model(model, reports, calc_deps=calc_deps, coverage_complete=bool(reports))
    findings = run_rules(EstateContext(model=model, analysis=analysis, reports=reports))
    bundle = ScanBundle(model=model, analysis=analysis, reports=reports, size=size, findings=findings)

    for warning in warnings:
        click.secho(f"warning: {warning}", fg="yellow")
    for disagreement in analysis.disagreements:
        click.secho(f"cross-check: {disagreement.object_id}: {disagreement.detail}", fg="cyan")
    click.echo(f"{len(analysis.by_status(STATUS_UNUSED))} unused object(s), {len(findings)} finding(s)")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        write_json(Path(out) / "pbi_lineage_export.json", [bundle])
        write_sqlite(Path(out) / "pbi_lineage.sqlite", [bundle])
        click.echo(f"export: {out}")


@main.command()
@click.option("--client-id", required=True)
@click.option("--tenant-id", required=True)
@click.option("--client-secret", default=None, help="Service principal secret (omit for device code)")
@click.option("--token", default=None, help="Use an existing bearer token instead of MSAL")
@click.option("--out", type=click.Path(), required=True)
@click.option("--modified-since", default=None, help="ISO timestamp for an incremental scan")
@click.option("--delta-path", default=None, help="Lakehouse path for Delta output (Fabric)")
def tenant(
    client_id: str,
    tenant_id: str,
    client_secret: str | None,
    token: str | None,
    out: str,
    modified_since: str | None,
    delta_path: str | None,
) -> None:
    """Scan a whole tenant via the Scanner API (mode M5)."""
    provider = (
        StaticToken(token)
        if token
        else MsalTokenProvider(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_secret=client_secret,
        )
    )
    client = RestClient(HttpxTransport(), provider, bucket=TokenBucket(500, 3600.0))
    output = run_tenant_scan(client, out_dir=out, modified_since=modified_since, delta_path=delta_path)
    for warning in output.warnings:
        click.secho(f"warning: {warning}", fg="yellow")
    click.echo(output.summary())


@main.command("removal-plan")
@click.argument("path", type=click.Path(exists=True))
@click.option("--object", "objects", multiple=True, help="Table[Column] or [Measure] to remove")
@click.option("--out", type=click.Path(), default=None, help="Write the impact manifest here")
def removal_plan(path: str, objects: tuple[str, ...], out: str | None) -> None:
    """Preview the blast radius of removing objects, and emit TMSL."""
    output = analyze_local_file(path)
    if not output.bundles or output.bundles[0].analysis is None:
        raise click.ClickException("could not analyze the model")
    bundle = output.bundles[0]
    target_ids = []
    for reference in objects:
        if "[" in reference and not reference.startswith("["):
            table, column = reference.split("[", 1)
            target_ids.append(nid_column(table, column.rstrip("]")))
        else:
            target_ids.append(nid_measure(reference.strip("[]")))
    plan = plan_removal(bundle.analysis.graph, bundle.analysis.verdicts, target_ids, model=bundle.model)
    click.echo(plan.summary())
    if plan.blocked:
        click.secho("\nno script emitted — resolve the blockers first", fg="red")
    else:
        click.echo("\n" + plan.tmsl_script)
    if out:
        Path(out).write_text(json.dumps(plan.manifest, indent=2), encoding="utf-8")
        click.echo(f"\nimpact manifest: {out}")


@main.command("search-m")
@click.argument("path", type=click.Path(exists=True))
@click.argument("needle")
def search_m(path: str, needle: str) -> None:
    """Search all M (Power Query) code for a table or view name (C6)."""
    from pbi_lineage.readers import read_any

    result = read_any(path)
    if result.model is None:
        raise click.ClickException("no model metadata available in this file")
    index = MExpressionIndex()
    index.add_model(result.model)
    rows = index.impact_of(needle)
    if not rows:
        click.echo(f"no M code references '{needle}'")
        return
    for row in rows:
        click.echo(f"{row['item_name']} / {row['entry_name']} [{row['confidence']}]: {row['evidence']}")


@main.command("install-external-tool")
@click.option(
    "--dir",
    "target_dir",
    default="C:\\Program Files (x86)\\Common Files\\Microsoft Shared\\Power BI Desktop\\External Tools",
)
@click.option("--exe", "exe_path", required=True, help="Path to the launcher executable")
def install_external_tool(target_dir: str, exe_path: str) -> None:
    """Register the tool in Power BI Desktop's External Tools ribbon."""
    try:
        path = write_pbitool_manifest(
            Path(target_dir),
            name="Lineage Analyzer",
            description="Model dependency and lineage analysis",
            exe_path=exe_path,
        )
    except OSError as exc:
        raise click.ClickException(
            f"could not write the manifest ({exc}). A portable install cannot write to "
            "Program Files — use `pbi-lineage live` to attach to a running Desktop instead."
        ) from exc
    click.echo(f"installed: {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
