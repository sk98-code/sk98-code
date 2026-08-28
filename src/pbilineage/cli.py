"""pbilineage CLI.

pbilineage doctor                     check config, credentials and optional deps
pbilineage demo                       build the synthetic tenant graph (no network)
pbilineage scan                       full or incremental tenant scan
pbilineage serve                      run the API (and the UI, if it is built)
pbilineage push                       load a scanned graph into Neo4j
pbilineage search / impact / lineage   query a graph from the terminal
pbilineage explain dax|m              show what the parsers make of an expression
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from pbilineage import __version__
from pbilineage.config import ADMIN_API_HINT, Settings
from pbilineage.graph.store import InMemoryGraphStore
from pbilineage.models import Confidence, NodeKind

console = Console()

CONFIDENCE_STYLE = {
    Confidence.RESOLVED.value: "green",
    Confidence.HEURISTIC.value: "yellow",
    Confidence.OPAQUE.value: "red",
}


@click.group()
@click.version_option(__version__, prog_name="pbilineage")
def main() -> None:
    """Column-level lineage for Power BI / Microsoft Fabric tenants."""


def _load_store(graph_path: Path) -> InMemoryGraphStore:
    try:
        return InMemoryGraphStore.load(graph_path)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _print_stats(stats: dict) -> None:
    table = Table(title="graph", show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("nodes", str(stats.get("nodes", 0)))
    table.add_row("edges", str(stats.get("edges", 0)))
    for kind, count in (stats.get("nodes_by_kind") or {}).items():
        table.add_row(f"  {kind}", str(count))
    for confidence, count in (stats.get("lineage_edges_by_confidence") or {}).items():
        style = CONFIDENCE_STYLE.get(confidence, "white")
        table.add_row(f"  lineage/{confidence}", f"[{style}]{count}[/]")
    console.print(table)


# ---------------------------------------------------------------------------
@main.command()
@click.option("--env-file", default=".env", show_default=True, help="dotenv file to read")
def doctor(env_file: str) -> None:
    """Check configuration, credentials and optional dependencies."""
    settings = Settings.load(env_file)
    table = Table(title="configuration", header_style="bold")
    table.add_column("setting")
    table.add_column("value")
    for key, value in settings.redacted().items():
        table.add_row(key, str(value))
    console.print(table)

    gaps = settings.missing()
    if gaps:
        console.print(f"[red]missing:[/] {', '.join(gaps)}")
        console.print(f"[dim]{ADMIN_API_HINT}[/]")
    else:
        console.print("[green]service-principal configuration looks complete[/]")

    checks = [
        ("msal", "certificate auth + token caching", "pip install msal"),
        ("pyadomd", "XMLA / DMV path (needs .NET + ADOMD.NET)", "pip install pbilineage[xmla]"),
        ("neo4j", "Neo4j graph store", "pip install pbilineage[neo4j]"),
        ("sqlglot", "source SQL column lineage (phase 4)", "pip install pbilineage[sql]"),
    ]
    deps = Table(title="optional dependencies", header_style="bold")
    deps.add_column("package")
    deps.add_column("enables")
    deps.add_column("status")
    for module, purpose, install in checks:
        try:
            __import__(module)
            status = "[green]installed[/]"
        except ImportError:
            status = f"[yellow]absent[/] — {install}"
        deps.add_row(module, purpose, status)
    console.print(deps)
    console.print(
        "[dim]Without an XMLA endpoint every DAX dependency is heuristic; "
        "that is expected for Pro-only workspaces.[/]"
    )


@main.command()
@click.option("--out", default="out/lineage/demo-graph.json", show_default=True)
def demo(out: str) -> None:
    """Build the synthetic two-workspace tenant graph. No network, no tenant."""
    from pbilineage.demo.fixtures import build_demo_graph

    graph = build_demo_graph()
    store = InMemoryGraphStore(graph)
    path = store.save(out)
    _print_stats(graph.stats())
    if graph.warnings:
        console.print(f"[yellow]{len(graph.warnings)} warning(s)[/] — see 'pbilineage warnings'")
    console.print(f"wrote [bold]{path}[/]")
    console.print(f"[dim]try: pbilineage serve --graph {path}[/]")


@main.command()
@click.option("--env-file", default=".env", show_default=True)
@click.option("--incremental", is_flag=True, help="only re-scan workspaces changed since last run")
@click.option("--workspace", "workspaces", multiple=True, help="scan only these workspace ids")
@click.option("--out", default="", help="graph output path (default: PBI_GRAPH_PATH)")
@click.option("--state", "state_path", default="", help="checkpoint db (default: PBI_STATE_PATH)")
@click.option("--no-reports", is_flag=True, help="skip the report Export API (faster)")
@click.option("--merge/--replace", default=True, help="merge into an existing graph file")
def scan(
    env_file: str,
    incremental: bool,
    workspaces: tuple[str, ...],
    out: str,
    state_path: str,
    no_reports: bool,
    merge: bool,
) -> None:
    """Scan the tenant and write a lineage graph."""
    from pbilineage.auth.credentials import ClientCredentialProvider
    from pbilineage.clients.admin_api import PowerBIAdminClient
    from pbilineage.clients.xmla import XmlaClient
    from pbilineage.scan.orchestrator import ScanOrchestrator
    from pbilineage.scan.state import ScanState

    settings = Settings.load(env_file)
    if not settings.has_credentials:
        raise click.ClickException(
            f"missing service-principal configuration: {', '.join(settings.missing())}. "
            "Copy .env.example to .env and fill it in."
        )

    graph_path = Path(out or settings.graph_path)
    checkpoint_path = Path(state_path or settings.state_path)

    store = InMemoryGraphStore()
    if merge and graph_path.is_file():
        store = InMemoryGraphStore.load(graph_path)

    tokens = ClientCredentialProvider(settings)
    admin = PowerBIAdminClient(settings, tokens)
    xmla = XmlaClient(settings=settings)
    if not xmla.available:
        console.print(f"[yellow]XMLA unavailable:[/] {xmla.unavailable_reason()}")

    with ScanState(checkpoint_path) as state:
        orchestrator = ScanOrchestrator(settings, admin, xmla, state, store)
        with console.status("scanning tenant..."):
            report = orchestrator.run(
                workspace_ids=list(workspaces) or None,
                incremental=incremental,
                export_reports=not no_reports,
            )

    for error in report.errors:
        console.print(f"[red]error:[/] {error}")
    console.print(
        f"[bold]{report.mode}[/] scan: {report.workspaces_scanned}/{report.workspaces_requested} "
        f"workspaces, {report.datasets} models, {report.reports} reports "
        f"({report.reports_with_layout} with layout), {report.dataflows} dataflows "
        f"in {report.duration_seconds:.1f}s"
    )
    if report.routing:
        console.print("resolution paths: " + ", ".join(f"{k}={v}" for k, v in report.routing.items()))
    if report.warnings:
        console.print(f"[yellow]{len(report.warnings)} warning(s)[/]; first few:")
        for warning in report.warnings[:5]:
            console.print(f"  [dim]-[/] {warning}")

    _print_stats(report.graph_stats)
    path = orchestrator.store.save(graph_path)
    console.print(f"wrote [bold]{path}[/]")
    if report.errors:
        raise SystemExit(1)


@main.command()
@click.option("--graph", "graph_path", default="out/lineage/graph.json", show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--neo4j", is_flag=True, help="serve from Neo4j instead of the graph file")
@click.option("--env-file", default=".env", show_default=True)
def serve(graph_path: str, host: str, port: int, neo4j: bool, env_file: str) -> None:
    """Run the lineage API (and the UI, if it has been built)."""
    try:
        import uvicorn
    except ImportError as exc:
        raise click.ClickException(
            "the API server needs FastAPI and uvicorn: pip install pbilineage[api]"
        ) from exc

    from pbilineage.api.app import create_app

    if neo4j:
        from pbilineage.graph.neo4j_store import Neo4jGraphStore

        settings = Settings.load(env_file)
        if not settings.neo4j_uri:
            raise click.ClickException("NEO4J_URI is not set; cannot serve from Neo4j")
        store = Neo4jGraphStore(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
        )
        console.print(f"serving from Neo4j at [bold]{settings.neo4j_uri}[/]")
    else:
        store = _load_store(Path(graph_path))
        _print_stats(store.stats())

    console.print(f"API on [bold]http://{host}:{port}[/]  (docs at /docs)")
    uvicorn.run(create_app(store), host=host, port=port, log_level="info")


@main.command()
@click.option("--graph", "graph_path", default="out/lineage/graph.json", show_default=True)
@click.option("--env-file", default=".env", show_default=True)
@click.option("--wipe", is_flag=True, help="delete each scanned workspace before loading")
def push(graph_path: str, env_file: str, wipe: bool) -> None:
    """Load a scanned graph into Neo4j."""
    from pbilineage.graph.neo4j_store import Neo4jGraphStore

    settings = Settings.load(env_file)
    if not settings.neo4j_uri:
        raise click.ClickException("NEO4J_URI is not set; nothing to push to")
    store = _load_store(Path(graph_path))

    with Neo4jGraphStore(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
    ) as target:
        if wipe:
            workspace_ids = {node.workspace_id for node in store.graph.nodes.values() if node.workspace_id}
            for workspace_id in workspace_ids:
                target.delete_workspace(workspace_id)
            console.print(f"cleared {len(workspace_ids)} workspace(s)")
        with console.status("writing to Neo4j..."):
            target.write(store.graph)
        _print_stats(target.stats())
    console.print(f"pushed [bold]{graph_path}[/] to {settings.neo4j_uri}")


@main.command()
@click.argument("term")
@click.option("--graph", "graph_path", default="out/lineage/graph.json", show_default=True)
@click.option("--kind", "kinds", multiple=True, help="filter by node kind (repeatable)")
@click.option("--limit", default=20, show_default=True)
def search(term: str, graph_path: str, kinds: tuple[str, ...], limit: int) -> None:
    """Find nodes by name."""
    store = _load_store(Path(graph_path))
    try:
        wanted = [NodeKind(k) for k in kinds] or None
    except ValueError as exc:
        raise click.ClickException(f"{exc}; valid kinds: {', '.join(k.value for k in NodeKind)}") from exc

    results = store.search(term, wanted, limit)
    if not results:
        console.print(f"[yellow]nothing matched[/] '{term}'")
        return
    table = Table(header_style="bold")
    table.add_column("kind")
    table.add_column("name")
    table.add_column("path", overflow="fold")
    table.add_column("id", overflow="fold", style="dim")
    for node in results:
        table.add_row(node.kind.value, node.name, node.qualified_name, node.id)
    console.print(table)


@main.command()
@click.argument("node_id")
@click.option("--graph", "graph_path", default="out/lineage/graph.json", show_default=True)
@click.option("--depth", default=6, show_default=True)
def impact(node_id: str, graph_path: str, depth: int) -> None:
    """What breaks downstream if this column or measure changes?"""
    store = _load_store(Path(graph_path))
    if store.get_node(node_id) is None:
        raise click.ClickException(f"no node with id '{node_id}' (try 'pbilineage search')")
    summary = store.impact(node_id, depth=depth)

    root = summary["root"]
    console.print(f"[bold]{root['qualified_name'] or root['name']}[/] ({root['kind']})")
    console.print(
        f"{summary['total_downstream']} downstream object(s): "
        + ", ".join(f"{k}={v}" for k, v in summary["by_kind"].items())
    )
    breakdown = " ".join(
        f"[{CONFIDENCE_STYLE.get(k, 'white')}]{k}={v}[/]" for k, v in summary["by_confidence"].items()
    )
    if breakdown:
        console.print("path confidence: " + breakdown)

    if summary["affected"]:
        table = Table(header_style="bold")
        table.add_column("depth", justify="right")
        table.add_column("kind")
        table.add_column("object", overflow="fold")
        table.add_column("confidence")
        for item in summary["affected"]:
            style = CONFIDENCE_STYLE.get(str(item["confidence"]), "white")
            table.add_row(
                str(item["depth"]),
                str(item["kind"]),
                str(item["qualified_name"] or item["name"]),
                f"[{style}]{item['confidence']}[/]",
            )
        console.print(table)


@main.command()
@click.argument("node_id")
@click.option("--graph", "graph_path", default="out/lineage/graph.json", show_default=True)
@click.option(
    "--direction",
    type=click.Choice(["upstream", "downstream", "both"]),
    default="upstream",
    show_default=True,
)
@click.option("--depth", default=4, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="emit the subgraph as JSON")
def lineage(node_id: str, graph_path: str, direction: str, depth: int, as_json: bool) -> None:
    """Show the lineage subgraph around a node."""
    store = _load_store(Path(graph_path))
    if store.get_node(node_id) is None:
        raise click.ClickException(f"no node with id '{node_id}' (try 'pbilineage search')")
    subgraph = store.lineage(node_id, direction=direction, depth=depth)  # type: ignore[arg-type]

    if as_json:
        console.print_json(json.dumps(subgraph.to_dict()))
        return

    nodes = {node.id: node for node in subgraph.nodes}
    depths = subgraph.meta.get("depths", {})
    table = Table(title=f"{direction} lineage", header_style="bold")
    table.add_column("hop", justify="right")
    table.add_column("kind")
    table.add_column("object", overflow="fold")
    for node in sorted(subgraph.nodes, key=lambda n: (depths.get(n.id, 0), n.qualified_name)):
        table.add_row(str(depths.get(node.id, 0)), node.kind.value, node.qualified_name or node.name)
    console.print(table)

    edges = Table(title="edges", header_style="bold")
    edges.add_column("from", overflow="fold")
    edges.add_column("edge")
    edges.add_column("to", overflow="fold")
    edges.add_column("confidence")
    edges.add_column("evidence", style="dim")
    for edge in subgraph.edges:
        style = CONFIDENCE_STYLE.get(edge.confidence.value, "white")
        edges.add_row(
            nodes[edge.source].name if edge.source in nodes else edge.source,
            edge.kind.value,
            nodes[edge.target].name if edge.target in nodes else edge.target,
            f"[{style}]{edge.confidence.value}[/]",
            edge.evidence,
        )
    console.print(edges)


@main.command()
@click.option("--graph", "graph_path", default="out/lineage/graph.json", show_default=True)
@click.option("--limit", default=50, show_default=True)
def warnings(graph_path: str, limit: int) -> None:
    """Everywhere the scan could not see clearly."""
    store = _load_store(Path(graph_path))
    items = store.warnings()
    if not items:
        console.print("[green]no warnings[/]")
        return
    console.print(f"[yellow]{len(items)} warning(s)[/]")
    for warning in items[:limit]:
        console.print(f"  [dim]-[/] {warning}")
    if len(items) > limit:
        console.print(f"  [dim]... {len(items) - limit} more[/]")


@main.group()
def explain() -> None:
    """Show what the parsers make of an expression (useful for triage)."""


@explain.command("dax")
@click.argument("expression")
def explain_dax(expression: str) -> None:
    """Print the model references a DAX expression mentions."""
    from pbilineage.parsers.dax import extract_dax_references

    references = extract_dax_references(expression)
    if not references:
        console.print("[yellow]no model references found[/]")
        return
    table = Table(header_style="bold")
    table.add_column("kind")
    table.add_column("reference")
    for reference in references:
        table.add_row(reference.kind.value, reference.qualified())
    console.print(table)
    console.print(
        "[dim]This is the Pro-only fallback path; on capacity these come from "
        "DISCOVER_CALC_DEPENDENCY instead.[/]"
    )


@explain.command("m")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
def explain_m(source: str) -> None:
    """Print the steps, sources and column trace of an M query file."""
    from pbilineage.parsers.m_query import analyze_m_query

    analysis = analyze_m_query(Path(source).read_text(encoding="utf-8"), query_name=Path(source).stem)

    steps = Table(title="steps", header_style="bold")
    steps.add_column("#", justify="right")
    steps.add_column("step")
    steps.add_column("kind")
    steps.add_column("function")
    steps.add_column("note", style="dim", overflow="fold")
    for index, step in enumerate(analysis.steps, start=1):
        style = "red" if step.is_opaque else "white"
        steps.add_row(str(index), step.name, f"[{style}]{step.kind.value}[/]", step.function, step.note)
    console.print(steps)

    if analysis.sources:
        sources = Table(title="sources", header_style="bold")
        sources.add_column("kind")
        sources.add_column("location", overflow="fold")
        sources.add_column("native query", style="dim", overflow="fold")
        for source_ref in analysis.sources:
            sources.add_row(source_ref.kind, source_ref.display(), source_ref.native_query[:120])
        console.print(sources)

    if analysis.columns:
        columns = Table(title="column trace", header_style="bold")
        columns.add_column("column")
        columns.add_column("from source column(s)")
        columns.add_column("confidence")
        columns.add_column("transforms", style="dim", overflow="fold")
        for name, trace in sorted(analysis.columns.items()):
            style = CONFIDENCE_STYLE.get(trace.confidence.value, "white")
            columns.add_row(
                name,
                ", ".join(sorted(trace.source_columns)) or "-",
                f"[{style}]{trace.confidence.value}[/]",
                " -> ".join(trace.ops),
            )
        console.print(columns)

    if analysis.unrecognized:
        console.print(
            f"[red]opaque:[/] unrecognised transform(s): {', '.join(sorted(set(analysis.unrecognized)))}"
        )


if __name__ == "__main__":  # pragma: no cover
    main()
