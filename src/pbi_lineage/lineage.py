"""End-to-end lineage: data source → M query → table → column → measure →
visual → page → report (build spec C5/C6, §10).

The dependency graph already spans column → measure → visual → page →
report. What it lacked was the *upstream* end: which warehouse object or
file each table is loaded from. `attach_sources` reads that from the M
expression index and adds it to the same graph, so one traversal now runs
the whole chain, and `end_to_end_rows` flattens it into the grid a data
engineer actually reads before changing a source table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pbi_lineage.graph import DependencyGraph, Node, nid_column, nid_table
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.resolve import AnalysisResult
from pbi_lineage.schema import Model

_NESTED_CALL = re.compile(r"^\s*(?:[A-Za-z][\w.]*\s*\(\s*)+")


def _clean_argument(text: str) -> str:
    """M arguments arrive with their wrapping call text, e.g.
    `File.Contents("C:/x.xlsx"` — keep just the literal."""
    cleaned = _NESTED_CALL.sub("", text or "")
    return cleaned.strip().strip('"').strip()


def nid_source(label: str) -> str:
    return f"source:{label}"


@dataclass
class SourceRef:
    """One upstream object a table is loaded from."""

    label: str  # "dbo.FactSales" or "sales.csv"
    system: str  # "Sql.Database", "Csv.Document", …
    server: str | None = None
    database: str | None = None

    def display(self) -> str:
        """Short, non-repeating label. A file source' path already *is* the
        label, so it must not be appended to itself."""
        parts = [
            part
            for part in (self.server, self.database)
            if part and part != self.label and part not in self.label
        ]
        return f"{self.label} ({'.'.join(parts)})" if parts else self.label


def sources_for_table(index: MExpressionIndex, table_name: str) -> list[SourceRef]:
    """Upstream objects for one table, from its partition M."""
    refs: list[SourceRef] = []
    seen: set[str] = set()
    for entry in index.entries:
        if entry.kind != "partition" or entry.entry_name != table_name:
            continue
        for source in entry.sources:
            label = ".".join(p for p in (source.schema, source.item) if p)
            if not label:
                # A file or web source: the literal argument is a path or URL.
                # Show its last segment so the column stays readable, and keep
                # the full location as the server context.
                raw = _clean_argument(source.arguments[-1]) if source.arguments else source.function
                label = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or raw
            if label in seen:
                continue
            seen.add(label)
            server = _clean_argument(source.arguments[0]) if source.arguments else None
            database = _clean_argument(source.arguments[1]) if len(source.arguments) > 1 else None
            if server and label and server.endswith(label):
                # file source: the "server" is the folder the file sits in
                server = server.replace("\\", "/").rsplit("/", 1)[0] or None
                database = None
            refs.append(SourceRef(label=label, system=source.function, server=server, database=database))
    return refs


def attach_sources(
    graph: DependencyGraph, model: Model, index: MExpressionIndex
) -> dict[str, list[SourceRef]]:
    """Add source nodes and `table → source` edges to an existing graph."""
    by_table: dict[str, list[SourceRef]] = {}
    for table in model.tables:
        refs = sources_for_table(index, table.name)
        if not refs:
            continue
        by_table[table.name] = refs
        for ref in refs:
            node_id = nid_source(ref.display())
            graph.add_node(
                Node(node_id, "source", ref.display(), meta={"system": ref.system, "object": ref.label})
            )
            graph.add_edge(
                nid_table(table.name),
                node_id,
                "defines",
                "parsed",
                f"{ref.system} in the partition of {table.name}",
            )
    return by_table


@dataclass
class LineageRow:
    """One end-to-end path, flattened for a grid."""

    source: str | None
    system: str | None
    table: str
    column: str
    status: str
    measures: list[str] = field(default_factory=list)
    visual: str | None = None
    visual_type: str | None = None
    page: str | None = None
    report: str | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "system": self.system,
            "table": self.table,
            "column": self.column,
            "status": self.status,
            "measures": self.measures,
            "visual": self.visual,
            "visual_type": self.visual_type,
            "page": self.page,
            "report": self.report,
        }


def end_to_end_rows(
    model: Model, analysis: AnalysisResult, index: MExpressionIndex, *, table: str = "", column: str = ""
) -> list[LineageRow]:
    """Flatten the graph into source → … → visual rows, one per consuming
    visual (or a single row when nothing consumes the column, which is what
    makes an unused column visible in the same view)."""
    graph = analysis.graph
    sources = {name: refs for name, refs in attach_sources(graph, model, index).items()}
    rows: list[LineageRow] = []

    for model_table in model.tables:
        if table and model_table.name != table:
            continue
        table_sources = sources.get(model_table.name) or [None]
        for model_column in model_table.columns:
            if column and model_column.name != column:
                continue
            node_id = nid_column(model_table.name, model_column.name)
            verdict = analysis.verdicts.get(node_id)
            status = verdict.status if verdict else "?"

            consumers = graph.consumers(node_id)
            measures = sorted(
                graph.nodes[c].name
                for c in consumers
                if graph.nodes.get(c) and graph.nodes[c].kind == "measure"
            )
            visuals = [
                graph.nodes[c] for c in consumers if graph.nodes.get(c) and graph.nodes[c].kind == "visual"
            ]

            for source in table_sources:
                if not visuals:
                    rows.append(
                        LineageRow(
                            source=source.display() if source else None,
                            system=source.system if source else None,
                            table=model_table.name,
                            column=model_column.name,
                            status=status,
                            measures=measures,
                        )
                    )
                    continue
                for visual in visuals:
                    page = graph.nodes.get(visual.parent) if visual.parent else None
                    report = graph.nodes.get(page.parent) if page and page.parent else None
                    rows.append(
                        LineageRow(
                            source=source.display() if source else None,
                            system=source.system if source else None,
                            table=model_table.name,
                            column=model_column.name,
                            status=status,
                            measures=measures,
                            visual=visual.id.rsplit("/", 1)[-1],
                            visual_type=visual.name,
                            page=page.name if page else None,
                            report=report.name if report else None,
                        )
                    )
    rows.sort(key=lambda r: (r.table, r.column, r.page or "", r.visual or ""))
    return rows


# ---------------------------------------------------------------------------
# Item-level lineage: sources → models/dataflows → consumers
# ---------------------------------------------------------------------------
#
# The coarse, top-down view that answers "what breaks if we retire this
# database?" — whole items rather than columns. Two orientations over the
# same graph, because the two questions people actually ask are opposite
# ends of it:
#
#   data sources view  — start at a server/database/file, walk downstream
#   models view        — start at a model, show upstream *and* downstream
#
# Both builders emit the same shape so one renderer serves both, and both
# work from a single local file or a whole tenant scan.


def _consumer_entry(kind: str, name: str, workspace: str = "", detail: str = "") -> dict:
    return {"kind": kind, "name": name, "workspace": workspace, "detail": detail}


def local_item_lineage(model: Model, index: MExpressionIndex, reports: list) -> dict:
    """Item lineage for one analyzed file."""
    source_rows: dict[str, dict] = {}
    upstream: list[dict] = []

    for table in model.tables:
        for ref in sources_for_table(index, table.name):
            entry = source_rows.setdefault(
                ref.display(),
                {
                    "source": ref.display(),
                    "system": ref.system,
                    "type": _source_type(ref.system),
                    "models": [],
                    "model_count": 0,
                    "report_count": 0,
                },
            )
            if all(m["model"] != model.name for m in entry["models"]):
                entry["models"].append(
                    {
                        "model": model.name,
                        "workspace": "",
                        "tables": [],
                        "consumers": [
                            _consumer_entry("report", report.name or "report") for report in reports
                        ],
                    }
                )
            member = next(m for m in entry["models"] if m["model"] == model.name)
            if table.name not in member["tables"]:
                member["tables"].append(table.name)
            entry["model_count"] = len(entry["models"])
            entry["report_count"] = len(reports)
            if all(u["source"] != ref.display() for u in upstream):
                upstream.append({"source": ref.display(), "system": ref.system})

    models = [
        {
            "model": model.name,
            "workspace": "",
            "kind": "semantic model",
            "upstream": upstream,
            "downstream": [_consumer_entry("report", report.name or "report") for report in reports],
            "cross_workspace": [],
        }
    ]
    return {"data_sources": sorted(source_rows.values(), key=lambda r: r["source"]), "models": models}


_SOURCE_TYPES = (
    ("Sql", "database"),
    ("Oracle", "database"),
    ("PostgreSQL", "database"),
    ("MySQL", "database"),
    ("Snowflake", "database"),
    ("AmazonRedshift", "database"),
    ("GoogleBigQuery", "database"),
    ("Databricks", "lakehouse"),
    ("Lakehouse", "lakehouse"),
    ("Fabric", "lakehouse"),
    ("Odbc", "database"),
    ("OleDb", "database"),
    ("Web", "web"),
    ("SharePoint", "file"),
    ("AzureStorage", "file"),
    ("Excel", "file"),
    ("Csv", "file"),
    ("File", "file"),
    ("Folder", "file"),
    ("PowerPlatform", "dataflow"),
    ("Dataflows", "dataflow"),
    ("PowerBI", "semantic model"),
)


def _source_type(system: str | None) -> str:
    for prefix, label in _SOURCE_TYPES:
        if (system or "").startswith(prefix):
            return label
    return "other"


def tenant_item_lineage(scan: dict, consumer_index: dict | None = None) -> dict:
    """Item lineage across a tenant scan payload.

    Uses the Scanner's own `datasourceInstances` / `upstreamDataflows`
    lineage where present, and falls back to parsing each model's M for
    its sources. Cross-workspace dependencies are called out explicitly —
    they are what quietly breaks workspace reorganizations.
    """
    from pbi_lineage.service.thin_reports import build_consumer_index  # noqa: PLC0415 - cycle

    consumer_index = consumer_index if consumer_index is not None else build_consumer_index(scan)
    workspaces = scan.get("workspaces", []) or []
    workspace_of: dict[str, str] = {}
    source_rows: dict[str, dict] = {}
    models: list[dict] = []

    datasource_by_id = {
        str(d.get("datasourceId") or d.get("datasourceInstanceId") or ""): d
        for d in (scan.get("datasourceInstances", []) or [])
    }

    for workspace in workspaces:
        workspace_name = workspace.get("name", "")
        for dataset in workspace.get("datasets", []) or []:
            workspace_of[str(dataset.get("id", ""))] = workspace_name

    for workspace in workspaces:
        workspace_name = workspace.get("name", "")
        for dataset in workspace.get("datasets", []) or []:
            dataset_id = str(dataset.get("id", ""))
            upstream: list[dict] = []

            for instance in dataset.get("datasourceUsages", []) or []:
                raw = datasource_by_id.get(str(instance.get("datasourceInstanceId", "")), {})
                details = raw.get("connectionDetails", {}) or {}
                label = ".".join(
                    str(part)
                    for part in (details.get("server"), details.get("database"), details.get("path"))
                    if part
                ) or raw.get("datasourceType", "unknown source")
                upstream.append({"source": label, "system": raw.get("datasourceType", "")})

            for flow in dataset.get("upstreamDataflows", []) or []:
                upstream.append({"source": str(flow.get("targetDataflowId", "")), "system": "Dataflow"})

            consumers = []
            cross_workspace = []
            for consumer in consumer_index.get(dataset_id, []):
                entry = _consumer_entry(consumer.consumer_type.value, consumer.name, consumer.workspace_name)
                consumers.append(entry)
                if consumer.workspace_name and consumer.workspace_name != workspace_name:
                    cross_workspace.append(entry)

            models.append(
                {
                    "model": dataset.get("name", ""),
                    "workspace": workspace_name,
                    "kind": "semantic model",
                    "upstream": upstream,
                    "downstream": consumers,
                    "cross_workspace": cross_workspace,
                }
            )

            for source in upstream:
                row = source_rows.setdefault(
                    source["source"],
                    {
                        "source": source["source"],
                        "system": source["system"],
                        "type": _source_type(source["system"]),
                        "models": [],
                        "model_count": 0,
                        "report_count": 0,
                    },
                )
                row["models"].append(
                    {
                        "model": dataset.get("name", ""),
                        "workspace": workspace_name,
                        "tables": [],
                        "consumers": consumers,
                    }
                )
                row["model_count"] = len(row["models"])
                row["report_count"] = sum(len(m["consumers"]) for m in row["models"])

        for dataflow in workspace.get("dataflows", []) or []:
            models.append(
                {
                    "model": dataflow.get("name", ""),
                    "workspace": workspace_name,
                    "kind": "dataflow",
                    "upstream": [],
                    "downstream": [],
                    "cross_workspace": [],
                }
            )

    return {
        "data_sources": sorted(source_rows.values(), key=lambda r: -r["model_count"]),
        "models": sorted(models, key=lambda m: (m["workspace"], m["model"])),
    }


# ---------------------------------------------------------------------------
# Column lineage tree: server → database → schema → table → column →
# semantic model → model column → relationship / visual / filter
# ---------------------------------------------------------------------------


_SYSTEM_LABELS = {
    "Sql.Database": "SQL Server",
    "PostgreSQL.Database": "PostgreSQL",
    "Oracle.Database": "Oracle",
    "MySQL.Database": "MySQL",
    "Snowflake.Databases": "Snowflake",
    "AmazonRedshift.Database": "Amazon Redshift",
    "GoogleBigQuery.Database": "BigQuery",
    "Databricks.Catalogs": "Databricks",
    "Databricks.Query": "Databricks",
    "Lakehouse.Contents": "Lakehouse",
    "Fabric.Warehouse": "Fabric Warehouse",
    "Excel.Workbook": "Excel",
    "Csv.Document": "CSV",
    "Odbc.DataSource": "ODBC",
    "Odbc.Query": "ODBC",
    "Web.Contents": "Web",
    "PowerPlatform.Dataflows": "Dataflow",
    "Dataflows.Contents": "Dataflow",
    "PowerBI.Datamarts": "Power BI Datamart",
}


def _system_label(function: str | None) -> str:
    return _SYSTEM_LABELS.get(function or "", (function or "Source").split(".")[0])


def _node(name: str, kind: str, source: str = "", status: str = "", **extra) -> dict:
    node = {"name": name, "type": kind, "source": source, "status": status, "children": []}
    node.update(extra)
    return node


def column_lineage_tree(model: Model, analysis: AnalysisResult, index: MExpressionIndex) -> list[dict]:
    """The full drill-down, shaped for a four-column tree view.

    Rows carry Name / Type / Source / Status, exactly as a lineage grid
    reads: the Source column names the *parent* item, so a reader can see
    the containment without following indentation alone.
    """
    from pbi_lineage.mtrace import trace_model  # noqa: PLC0415 — keeps the import graph flat

    traces = trace_model(model, index)
    graph = analysis.graph

    # source-column -> the model columns derived from it
    derived: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    unresolved: list[tuple[str, str, str]] = []
    for table_name, trace in traces.items():
        for column_name, origin in trace.columns.items():
            if origin.source_column and origin.source_table:
                key = (trace.source_table or "", origin.source_column, origin.derivation)
                derived.setdefault(key[:2] + ("",), []).append((table_name, column_name))
            else:
                unresolved.append((table_name, column_name, origin.derivation))

    # group the model's sources into server → database → schema → table
    roots: dict[str, dict] = {}
    for table in model.tables:
        trace = traces.get(table.name)
        for ref in sources_for_table(index, table.name):
            system = _system_label(ref.system)
            server = ref.server or system
            root = roots.setdefault(server, _node(server, f"{system} Server", "", "", key=f"server:{server}"))
            database = ref.database or ""
            db_node = _find_or_add(root, database or system, f"{system} Database", f"{server} (Server)")
            schema = (trace.source_schema if trace else None) or ""
            parent = db_node
            if schema:
                parent = _find_or_add(db_node, schema, f"{system} Schema", f"{database or system} (Database)")
            table_label = (trace.source_table if trace else None) or ref.label
            table_label = table_label.split(".")[-1]
            table_node = _find_or_add(
                parent,
                table_label,
                f"{system} Table",
                f"{schema or database or system} ({'Schema' if schema else 'Database'})",
            )
            _fill_source_columns(table_node, table, trace, traces, model, analysis, graph, system)
    return list(roots.values())


def _single_input(detail: str) -> str | None:
    """The one column a `computed from X` detail names, if it names exactly one."""
    marker = "computed from "
    if not detail.startswith(marker):
        return None
    inputs = [part.strip() for part in detail[len(marker) :].split(",") if part.strip()]
    return inputs[0] if len(inputs) == 1 else None


def _find_or_add(parent: dict, name: str, kind: str, source: str) -> dict:
    for child in parent["children"]:
        if child["name"] == name and child["type"] == kind:
            return child
    child = _node(name, kind, source)
    parent["children"].append(child)
    return child


def _fill_source_columns(table_node, table, trace, traces, model, analysis, graph, system) -> None:
    """Under a source table: one row per source column, then the semantic
    model and the model columns derived from it, then their consumers."""
    if trace is None:
        return
    by_source: dict[str, list[tuple[str, str]]] = {}
    for column_name, origin in trace.columns.items():
        if origin.source_column:
            by_source.setdefault(origin.source_column, []).append((table.name, column_name))

    # A computed column belongs under the column it was computed *from*,
    # not in the untraced bucket — that nesting is the derivation chain a
    # reader follows ("GrossAmount -> AdjustedRiskAmount").
    computed_under: dict[str, list[tuple[str, str]]] = {}
    for column_name, origin in trace.columns.items():
        if origin.source_column or origin.derivation != "computed":
            continue
        parent_column = _single_input(origin.detail)
        parent_origin = trace.columns.get(parent_column) if parent_column else None
        if parent_origin is not None and parent_origin.source_column:
            computed_under.setdefault(parent_origin.source_column, []).append((table.name, column_name))

    for source_column, members in sorted(by_source.items()):
        column_node = _find_or_add(
            table_node, source_column, f"{system} column", f"{table_node['name']} (Table)"
        )
        model_node = _find_or_add(column_node, model.name, "Semantic Model", "")
        for table_name, model_column in members:
            origin = traces[table_name].columns[model_column]
            node_id = nid_column(table_name, model_column)
            verdict = analysis.verdicts.get(node_id)
            status = verdict.status if verdict else ""
            child = _node(
                model_column,
                "Model column",
                f"{table_name} (Table)",
                status,
                key=node_id,
                derivation=origin.derivation,
                detail=origin.detail,
            )
            _fill_consumers(child, node_id, graph)
            for derived_table, derived_column in computed_under.get(source_column, []):
                derived_origin = traces[derived_table].columns[derived_column]
                derived_id = nid_column(derived_table, derived_column)
                derived_verdict = analysis.verdicts.get(derived_id)
                grandchild = _node(
                    derived_column,
                    "Model column",
                    f"{derived_table} (Table)",
                    derived_verdict.status if derived_verdict else "",
                    key=derived_id,
                    derivation=derived_origin.derivation,
                    detail=derived_origin.detail,
                )
                _fill_consumers(grandchild, derived_id, graph)
                child["children"].append(grandchild)
            model_node["children"].append(child)

    # columns whose origin could not be traced still belong in the tree —
    # hiding them would quietly overstate coverage
    nested = {column for members in computed_under.values() for _, column in members}
    untraced = [
        (name, origin)
        for name, origin in trace.columns.items()
        if not origin.source_column and name not in nested
    ]
    if untraced:
        bucket = _find_or_add(table_node, "(origin not traced)", "Unmapped", f"{table_node['name']} (Table)")
        model_node = _find_or_add(bucket, model.name, "Semantic Model", "")
        for name, origin in sorted(untraced):
            node_id = nid_column(table.name, name)
            verdict = analysis.verdicts.get(node_id)
            child = _node(
                name,
                "Model column",
                f"{table.name} (Table)",
                verdict.status if verdict else "",
                key=node_id,
                derivation=origin.derivation,
                detail=origin.detail,
            )
            _fill_consumers(child, node_id, graph)
            model_node["children"].append(child)


_CONSUMER_LABELS = {
    "projects": "Used in visual",
    "filters": "Used in visual level filter",
    "sorts": "Used as sort",
    "formats": "Used in conditional formatting",
    "relates": "Used in relationship",
    "defines": "Used in calculation",
    "wildcard": "Dynamic reference",
}


def _fill_consumers(node: dict, node_id: str, graph) -> None:
    """Everything that consumes this model column, labelled by edge kind —
    the 'Used in visual / relationship / filter' rows."""
    seen: set[tuple[str, str]] = set()
    for edge in graph.in_edges(node_id):
        consumer = graph.nodes.get(edge.source)
        if consumer is None:
            continue
        label = _CONSUMER_LABELS.get(edge.kind, "Used by")
        if consumer.kind == "measure":
            child = _node(consumer.name, "Model measure", f"used by {edge.kind}", "")
            _fill_consumers(child, edge.source, graph)
        elif consumer.kind == "visual":
            page = graph.nodes.get(consumer.parent) if consumer.parent else None
            child = _node(consumer.name, label, page.name if page else "", "")
        elif consumer.kind == "relationship":
            child = _node(consumer.name, "Used in relationship", edge.evidence, "")
        elif consumer.kind == "role":
            child = _node(consumer.name, "Used in RLS", edge.evidence, "")
        elif consumer.kind in ("hierarchy", "column", "calc_item", "table"):
            child = _node(consumer.name, f"Used in {consumer.kind}", edge.evidence, "")
        else:
            continue
        key = (child["name"], child["type"])
        if key in seen:
            continue
        seen.add(key)
        node["children"].append(child)
