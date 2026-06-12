"""bimigrate CLI — the four operating modes plus repository management.

bimigrate discover  <paths...>   inventory + scoring + dedup + reports
bimigrate assess    <paths...>   discovery + feature mapping matrix + effort
bimigrate convert   <paths...>   PBIP/TMDL/DAX/M generation w/ confidence tiers
bimigrate validate  <paths...>   parity/structural checks + exception register
bimigrate kb        init|stats|export|add-rule
bimigrate mappings  export
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from bimigrate.engine.bulk import BulkRunner
from bimigrate.engine.dedup import cluster_inventories

console = Console()


@click.group()
def main() -> None:
    """Unified BI migration platform: Tableau / Spotfire / QlikView / Qlik Sense -> Power BI."""


def _run_bulk(paths: tuple[str, ...], job_db: str, workers: int, scrub: bool) -> BulkRunner:
    runner = BulkRunner(Path(job_db), workers=workers, scrub=scrub)
    files = runner.discover_files([Path(p) for p in paths])
    new = runner.enqueue(files)
    console.print(f"[bold]{len(files)}[/] source files found, [bold]{new}[/] new jobs queued")
    with console.status("parsing estate..."):
        stats = runner.run()
    console.print(
        f"parsed: [green]{stats['done']}[/] ok, [red]{stats['error']}[/] failed, "
        f"{stats['skipped_existing']} already done (resumable job db: {job_db})"
    )
    return runner


@main.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--out", default="out/discovery", show_default=True, help="report output dir")
@click.option("--job-db", default="out/discovery/jobs.db", show_default=True)
@click.option("--workers", default=4, show_default=True)
@click.option("--scrub", is_flag=True, help="strip sensitive metadata before any output")
@click.option("--dedup-threshold", default=0.8, show_default=True)
def discover(paths, out, job_db, workers, scrub, dedup_threshold) -> None:
    """Discovery Mode: inventory + complexity scoring + migration readiness."""
    Path(job_db).parent.mkdir(parents=True, exist_ok=True)
    runner = _run_bulk(paths, job_db, workers, scrub)
    inventories = runner.load_inventories()
    clusters = cluster_inventories(inventories, threshold=dedup_threshold)

    from bimigrate.report import write_reports

    written = write_reports(inventories, clusters, Path(out), runner.errors())
    runner.close()

    table = Table(title="Estate summary")
    table.add_column("Artifact")
    table.add_column("Tool")
    table.add_column("Band")
    table.add_column("Score", justify="right")
    for inv in sorted(inventories, key=lambda i: -i.complexity_score)[:20]:
        table.add_row(
            inv.artifact_name, inv.source_tool.value, inv.complexity_band.value, f"{inv.complexity_score:.1f}"
        )
    console.print(table)
    for path in written:
        console.print(f"  wrote [cyan]{path}[/]")


@main.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--out", default="out/assessment", show_default=True)
@click.option("--job-db", default="out/assessment/jobs.db", show_default=True)
@click.option("--workers", default=4, show_default=True)
@click.option("--scrub", is_flag=True)
def assess(paths, out, job_db, workers, scrub) -> None:
    """Assessment Mode: discovery + feature mapping matrix + effort estimation."""
    from bimigrate.engine.complexity import effort_hours
    from bimigrate.mapping import MappingRepository
    from bimigrate.report import write_reports

    out_dir = Path(out)
    Path(job_db).parent.mkdir(parents=True, exist_ok=True)
    runner = _run_bulk(paths, job_db, workers, scrub)
    inventories = runner.load_inventories()
    clusters = cluster_inventories(inventories)
    write_reports(inventories, clusters, out_dir, runner.errors())
    runner.close()

    repo = MappingRepository(out_dir / "mappings.db")
    repo.init_from_seeds()
    repo.export_excel(out_dir / "feature_mapping_matrix.xlsx")
    repo.export_json(out_dir / "feature_mapping_matrix.json")

    unsupported_rows = []
    tools_in_estate = {inv.source_tool.value for inv in inventories}
    for tool in tools_in_estate:
        for m in repo.unsupported(tool):
            unsupported_rows.append(
                {"source_tool": tool, "feature": m.feature_name, "recommendation": m.fallback_strategy}
            )
    (out_dir / "unsupported_features.json").write_text(
        json.dumps(unsupported_rows, indent=2), encoding="utf-8"
    )
    repo.close()

    total_effort = sum(effort_hours(inv) for inv in inventories)
    console.print(
        f"total estimated effort: [bold]{total_effort:.0f}h[/] " f"across {len(inventories)} artifacts"
    )
    console.print(f"  wrote [cyan]{out_dir / 'feature_mapping_matrix.xlsx'}[/]")
    console.print(f"  wrote [cyan]{out_dir / 'unsupported_features.json'}[/]")


@main.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--out", default="out/converted", show_default=True)
@click.option("--job-db", default="out/converted/jobs.db", show_default=True)
@click.option("--workers", default=4, show_default=True)
@click.option("--scrub", is_flag=True)
def convert(paths, out, job_db, workers, scrub) -> None:
    """Conversion Mode: PBIP/TMDL/M/DAX generation with confidence tiers."""
    from bimigrate.convert import ConversionEngine
    from bimigrate.emit.model_builder import build_model
    from bimigrate.emit.pbip import PbipEmitter
    from bimigrate.kb import KnowledgeBase
    from bimigrate.models.core import ConfidenceTier

    out_dir = Path(out)
    Path(job_db).parent.mkdir(parents=True, exist_ok=True)
    runner = _run_bulk(paths, job_db, workers, scrub)
    inventories = runner.load_inventories()
    runner.close()

    kb = KnowledgeBase(out_dir / "kb.db" if out_dir.exists() else ":memory:")
    kb.init_from_seeds()
    engine = ConversionEngine(kb)
    emitter = PbipEmitter(out_dir)

    all_decisions = []
    tier_counts = {t.value: 0 for t in ConfidenceTier}
    for inv in inventories:
        decisions = engine.convert_inventory(inv)
        all_decisions.extend(d.model_dump(mode="json") for d in decisions)
        for d in decisions:
            tier_counts[d.tier.value] += 1
        model = build_model(inv, decisions)
        project = emitter.emit(inv, model)
        console.print(f"  emitted [cyan]{project}[/] " f"({len(decisions)} expression decisions)")

    (out_dir / "conversion_decisions.json").write_text(json.dumps(all_decisions, indent=2), encoding="utf-8")
    kb.close()
    console.print(
        f"decisions: [green]{tier_counts['AUTO']} AUTO[/], "
        f"[yellow]{tier_counts['ASSISTED']} ASSISTED[/], "
        f"[red]{tier_counts['MANUAL']} MANUAL[/] "
        f"-> {out_dir / 'conversion_decisions.json'}"
    )


@main.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--out", default="out/validation", show_default=True)
@click.option("--job-db", default="out/validation/jobs.db", show_default=True)
@click.option("--workers", default=4, show_default=True)
def validate(paths, out, job_db, workers) -> None:
    """Validation Mode: structural + metadata checks, accuracy per tier/band."""
    from bimigrate.convert import ConversionEngine
    from bimigrate.kb import KnowledgeBase
    from bimigrate.validate import ValidationReport, Validator

    out_dir = Path(out)
    Path(job_db).parent.mkdir(parents=True, exist_ok=True)
    runner = _run_bulk(paths, job_db, workers, scrub=False)
    inventories = runner.load_inventories()
    runner.close()

    kb = KnowledgeBase()
    kb.init_from_seeds()
    engine = ConversionEngine(kb)
    validator = Validator()
    report = ValidationReport()

    for inv in inventories:
        validator.check_metadata_roundtrip(inv, report)
        decisions = engine.convert_inventory(inv)
        validator.register_decisions(decisions, inv.complexity_band, report)
    kb.close()

    matrix = report.accuracy_matrix()
    table = Table(title="Accuracy per tier x complexity band (never blended)")
    for col in ("Tier", "Band", "Total", "Passed", "Accuracy %"):
        table.add_column(col)
    for row in matrix:
        table.add_row(
            row["tier"],
            row["complexity_band"],
            str(row["total"]),
            str(row["passed"]),
            str(row["accuracy_pct"]),
        )
    console.print(table)
    console.print(f"automated conversion rate: " f"[bold]{report.automated_conversion_rate}%[/]")

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "accuracy_matrix": matrix,
        "automated_conversion_rate": report.automated_conversion_rate,
        "exception_register": [vars(e) | {"reason_code": e.reason_code.value} for e in report.exceptions],
        "structural_checks": report.structural_checks,
    }
    path = out_dir / "validation_report.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    console.print(f"  wrote [cyan]{path}[/] " f"({len(report.exceptions)} exception-register entries)")


@main.group()
def kb() -> None:
    """Knowledge-base management."""


@kb.command("init")
@click.option("--db", default="out/kb.db", show_default=True)
@click.option("--export-dir", default="out/kb", show_default=True)
def kb_init(db, export_dir) -> None:
    """Build the KB from seeds; export JSON + Markdown."""
    from bimigrate.kb import KnowledgeBase

    Path(db).parent.mkdir(parents=True, exist_ok=True)
    knowledge = KnowledgeBase(db)
    counts = knowledge.init_from_seeds()
    written = knowledge.export_json(Path(export_dir))
    written.append(knowledge.export_markdown(Path(export_dir)))
    knowledge.close()
    console.print(f"KB built at [cyan]{db}[/]: {counts}")
    for path in written:
        console.print(f"  wrote [cyan]{path}[/]")


@kb.command("stats")
@click.option("--db", default="out/kb.db", show_default=True)
def kb_stats(db) -> None:
    from bimigrate.kb import KnowledgeBase

    knowledge = KnowledgeBase(db)
    console.print(knowledge.stats())
    knowledge.close()


@kb.command("add-rule")
@click.option("--db", default="out/kb.db", show_default=True)
@click.argument("rule_json", type=click.Path(exists=True))
def kb_add_rule(db, rule_json) -> None:
    """Extend the KB with a ConversionRule from a JSON file."""
    from bimigrate.kb import KnowledgeBase
    from bimigrate.models.core import ConversionRule

    rule = ConversionRule.model_validate_json(Path(rule_json).read_text())
    knowledge = KnowledgeBase(db)
    knowledge.add_rule(rule)
    knowledge.close()
    console.print(f"added rule [bold]{rule.rule_id}[/] ({rule.tier})")


@main.group()
def mappings() -> None:
    """Feature Mapping Repository management."""


@mappings.command("export")
@click.option("--db", default="out/mappings.db", show_default=True)
@click.option("--out", default="out/mappings", show_default=True)
def mappings_export(db, out) -> None:
    from bimigrate.mapping import MappingRepository

    Path(db).parent.mkdir(parents=True, exist_ok=True)
    repo = MappingRepository(db)
    count = repo.init_from_seeds()
    out_dir = Path(out)
    excel = repo.export_excel(out_dir / "feature_mapping_matrix.xlsx")
    json_path = repo.export_json(out_dir / "feature_mapping_matrix.json")
    repo.close()
    console.print(f"{count} mappings -> [cyan]{excel}[/], [cyan]{json_path}[/]")


@main.command()
@click.option("--out", default="out/benchmark", show_default=True)
@click.option(
    "--corpus",
    multiple=True,
    type=click.Path(exists=True),
    help="extra golden-case JSON files (pilot estates)",
)
def benchmark(out, corpus) -> None:
    """Run the conversion-accuracy benchmark corpus (regression gate for the KB)."""
    from bimigrate.validate.benchmark import run_benchmark

    result = run_benchmark(extra_files=[Path(c) for c in corpus])
    table = Table(title=f"Benchmark: {result.passed}/{result.total} cases passed")
    for col in ("Expected tier", "Band", "Total", "Passed", "Accuracy %"):
        table.add_column(col)
    for row in result.report.accuracy_matrix():
        table.add_row(
            row["tier"],
            row["complexity_band"],
            str(row["total"]),
            str(row["passed"]),
            str(row["accuracy_pct"]),
        )
    console.print(table)
    for failure in result.failures:
        console.print(
            f"[red]FAIL[/] [{failure['tool']}] {failure['expression'][:70]} "
            f"(expected {failure['expected_tier']}, got {failure['actual_tier']})"
        )
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "benchmark_report.json"
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    console.print(f"  wrote [cyan]{path}[/]")
    if result.failures:
        raise SystemExit(1)


@main.group()
def collect() -> None:
    """Server-side estate collectors (Tableau Server / Qlik Sense QRS)."""


@collect.command("tableau")
@click.option("--server", required=True, help="https://tableau.example.com")
@click.option("--site", default="", show_default=True, help="site contentUrl ('' = default)")
@click.option("--pat-name", required=True, envvar="TABLEAU_PAT_NAME")
@click.option("--pat-secret", required=True, envvar="TABLEAU_PAT_SECRET")
@click.option("--out", default="out/collected/tableau", show_default=True)
def collect_tableau(server, site, pat_name, pat_secret, out) -> None:
    """Download workbooks + schedules/subscriptions/alerts via the REST API."""
    from bimigrate.collect import TableauServerClient

    client = TableauServerClient(server, site)
    client.sign_in(pat_name, pat_secret)
    try:
        items = client.collect_estate(Path(out))
    finally:
        client.sign_out()
    workbooks = [i for i in items if i.kind == "workbook"]
    console.print(
        f"collected [bold]{len(workbooks)}[/] workbooks, "
        f"{len(items) - len(workbooks)} schedule/subscription/alert records -> [cyan]{out}[/]"
    )
    console.print("next: bimigrate discover " + out)


@collect.command("qliksense")
@click.option("--server", required=True, help="https://qlik.example.com")
@click.option("--user-directory", required=True, envvar="QLIK_USER_DIRECTORY")
@click.option("--user-id", required=True, envvar="QLIK_USER_ID")
@click.option("--virtual-proxy", default="", show_default=True)
@click.option("--out", default="out/collected/qliksense", show_default=True)
def collect_qliksense(server, user_directory, user_id, virtual_proxy, out) -> None:
    """Export apps + streams/reload tasks via the QRS API."""
    from bimigrate.collect import QlikQrsClient

    client = QlikQrsClient(server, user_directory, user_id, virtual_proxy)
    items = client.collect_estate(Path(out))
    apps = [i for i in items if i.kind == "app"]
    console.print(
        f"collected [bold]{len(apps)}[/] apps, "
        f"{len(items) - len(apps)} stream/task records -> [cyan]{out}[/]"
    )
    console.print("note: pair QVF binaries with Engine JSON exports for full fidelity")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8400, show_default=True)
def web(host, port) -> None:
    """Launch the interactive web app (requires the [web] extra)."""
    try:
        from bimigrate.web.app import run
    except ImportError as exc:
        raise click.ClickException(
            "web dependencies missing — install with: pip install 'bimigrate[web]'"
        ) from exc
    console.print(f"bimigrate web UI on [cyan]http://{host}:{port}[/] (Ctrl+C to stop)")
    run(host=host, port=port)


if __name__ == "__main__":
    main()
