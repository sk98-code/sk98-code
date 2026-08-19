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

from pbi_lineage.graph import DependencyGraph, Node, nid_column, nid_measure, nid_table
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
            if source.function in ("Table.FromRows", "Json.Document") and not source.item:
                # Data typed or pasted into the report itself. It has no
                # upstream location, and the honest label says exactly that
                # rather than leaving the table looking unexplained.
                label = "Entered data"
                if label in seen:
                    continue
                seen.add(label)
                refs.append(SourceRef(label=label, system=source.function))
                continue
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
    "AnalysisServices.Database": "Analysis Services",
    "AnalysisServices.Databases": "Analysis Services",
    "OData.Feed": "OData",
    "SharePoint.Files": "SharePoint",
    "SharePoint.Tables": "SharePoint",
    "Salesforce.Data": "Salesforce",
    "Salesforce.Reports": "Salesforce",
    "Access.Database": "Access",
    "Folder.Files": "Folder",
    "File.Contents": "File",
    "AzureStorage.Blobs": "Azure Blob Storage",
    "AzureStorage.DataLake": "Azure Data Lake",
    "Json.Document": "JSON",
    "Xml.Tables": "XML",
    "Table.FromRows": "Entered data",
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


# ---------------------------------------------------------------------------
# Node-graph shape of the same lineage
# ---------------------------------------------------------------------------

_DATAFLOW_SYSTEMS = {"PowerPlatform.Dataflows", "Dataflows.Contents", "PowerBI.Dataflows"}
_UPSTREAM_MODEL_SYSTEMS = {
    "AnalysisServices.Database",
    "AnalysisServices.Databases",
    "PowerBI.Datamarts",
    "PowerBIDatamarts.Contents",
}


class _GraphBuilder:
    """Accumulates cards (artifacts) and the field-to-field hops between them.

    A *card* is one artifact — a source table, a dataflow entity, the
    semantic model, a report. A *field* is one column or measure on that
    card. Edges join fields, not cards, which is the whole point: the
    picture has to answer "this column, where does it go", not "these two
    artifacts are related somehow".
    """

    def __init__(self) -> None:
        self.cards: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._edge_keys: set[tuple[str, str, str]] = set()

    def card(self, card_id: str, kind: str, name: str, badge: str, subtitle: str = "") -> dict:
        card = self.cards.get(card_id)
        if card is None:
            card = {
                "id": card_id,
                "kind": kind,
                "name": name,
                "badge": badge,
                "subtitle": subtitle,
                "fields": [],
                "lane": 0,
            }
            self.cards[card_id] = card
            card["_index"] = {}
        return card

    def field(self, card: dict, name: str, kind: str = "column", **extra) -> str:
        field_id = f"{card['id']}::{kind}::{name}"
        existing = card["_index"].get(field_id)
        if existing is not None:
            for key, value in extra.items():
                if value and not existing.get(key):
                    existing[key] = value
            return field_id
        entry = {"id": field_id, "name": name, "kind": kind, "status": "", "table": "", "detail": ""}
        entry.update({k: v for k, v in extra.items() if v is not None})
        card["fields"].append(entry)
        card["_index"][field_id] = entry
        return field_id

    def link(self, source: str, target: str, kind: str, evidence: str) -> None:
        key = (source, target, kind)
        if source == target or key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append({"source": source, "target": target, "kind": kind, "evidence": evidence})

    def result(self) -> dict:
        _assign_lanes(self.cards, self.edges)
        cards = sorted(self.cards.values(), key=lambda c: (c["lane"], c["name"].lower()))
        for card in cards:
            card.pop("_index", None)
        return {"nodes": cards, "edges": self.edges}


def _card_of(field_id: str) -> str:
    return field_id.split("::", 1)[0]


_LANE_FLOOR = {
    "source": 0,
    "dataflow": 1,
    "upstream_model": 1,
    "semantic_model": 2,
    "report": 3,
    "paginated": 3,
    "notebook": 3,
}


def _assign_lanes(cards: dict[str, dict], edges: list[dict]) -> None:
    """Lane = longest path *into* a card, floored by what kind of artifact
    it is. The path is what actually orders a dataflow between its
    warehouse and the model; the floor is what keeps a card whose columns
    could not be traced — so it has no edges at all — on the upstream side
    where it belongs instead of drifting to the end of the canvas."""
    incoming: dict[str, set[str]] = {card_id: set() for card_id in cards}
    for edge in edges:
        source, target = _card_of(edge["source"]), _card_of(edge["target"])
        if source != target and target in incoming and source in cards:
            incoming[target].add(source)

    memo: dict[str, int] = {}

    def depth(card_id: str, seen: frozenset[str]) -> int:
        if card_id in memo:
            return memo[card_id]
        if card_id in seen:  # a cycle cannot deepen the layout
            return 0
        best = 0
        for previous in incoming.get(card_id, ()):
            best = max(best, 1 + depth(previous, seen | {card_id}))
        memo[card_id] = best
        return best

    for card_id, card in cards.items():
        card["lane"] = max(depth(card_id, frozenset()), _LANE_FLOOR.get(card["kind"], 0))

    # A model with no dataflow leaves lane 1 empty; compact so the canvas
    # has no blank column between the source and the model.
    used = sorted({card["lane"] for card in cards.values()})
    compacted = {lane: i for i, lane in enumerate(used)}
    for card in cards.values():
        card["lane"] = compacted[card["lane"]]


def column_lineage_graph(
    model: Model,
    analysis: AnalysisResult,
    index: MExpressionIndex,
    reports: list | None = None,
) -> dict:
    """The lineage of `column_lineage_tree`, shaped for a node-graph canvas.

    Returns `{"nodes": [...cards...], "edges": [...field hops...]}`. Each
    card carries its own field list so the view can search and page within
    one artifact without another round trip; each edge names the two fields
    it joins and the evidence for the hop.
    """
    from pbi_lineage.mtrace import trace_model  # noqa: PLC0415 — keeps the import graph flat

    traces = trace_model(model, index)
    graph = analysis.graph
    builder = _GraphBuilder()

    model_card = builder.card(
        f"model:{model.name}",
        "semantic_model",
        model.name,
        "semantic model",
        f"{len(model.tables)} tables",
    )
    field_of: dict[str, str] = {}
    for table in model.tables:
        for column in table.columns:
            node_id = nid_column(table.name, column.name)
            verdict = analysis.verdicts.get(node_id)
            origin = traces.get(table.name).columns.get(column.name) if table.name in traces else None
            field_of[node_id] = builder.field(
                model_card,
                column.name,
                "column",
                table=table.name,
                status=verdict.status if verdict else "",
                key=node_id,
                detail=origin.detail if origin else "",
                derivation=origin.derivation if origin else "untraced",
            )
        for measure in table.measures:
            node_id = nid_measure(measure.name)
            verdict = analysis.verdicts.get(node_id)
            field_of[node_id] = builder.field(
                model_card,
                measure.name,
                "measure",
                table=table.name,
                status=verdict.status if verdict else "",
                key=node_id,
            )

    _add_source_cards(builder, model, index, traces, field_of)
    _add_report_cards(builder, graph, model_card, field_of, builder.link)
    _add_model_internal_links(builder, graph, field_of)
    return builder.result()


def _add_source_cards(builder, model, index, traces, field_of) -> None:
    """One card per upstream object, with the columns that were actually
    traced out of it. A column we could not follow gets no source field —
    the gap in the picture is the honest report of the gap in the trace."""
    for table in model.tables:
        trace = traces.get(table.name)
        for ref in sources_for_table(index, table.name):
            system = _system_label(ref.system)
            if ref.system in _DATAFLOW_SYSTEMS:
                kind, badge = "dataflow", "dataflow"
            elif ref.system in _UPSTREAM_MODEL_SYSTEMS:
                kind, badge = "upstream_model", "upstream semantic model"
            else:
                kind, badge = "source", f"{system} table"
            schema = (trace.source_schema if trace else None) or ""
            table_label = ((trace.source_table if trace else None) or ref.label).split(".")[-1]
            subtitle = " · ".join(p for p in (ref.server, ref.database, schema) if p)
            card = builder.card(
                f"src:{ref.server}|{ref.database}|{schema}|{table_label}",
                kind,
                table_label,
                badge,
                subtitle,
            )
            if trace is None:
                card.setdefault("untraced", 0)
                card.setdefault("untraced_reason", "no Power Query expression was found for this table")
                continue
            lost = 0
            for column_name, origin in trace.columns.items():
                if not origin.source_column:
                    lost += 1
                    continue
                source_field = builder.field(card, origin.source_column, "column", table=table_label)
                target = field_of.get(nid_column(table.name, column_name))
                if target:
                    builder.link(
                        source_field,
                        target,
                        origin.derivation,
                        origin.detail or f"traced through {len(origin.steps)} Power Query step(s)",
                    )
            if lost:
                # Say how many columns were lost and at which step. A card
                # that is simply empty reads as "no data here"; it has to
                # read as "the trace stopped here, and this is why".
                card["untraced"] = card.get("untraced", 0) + lost
                card.setdefault(
                    "untraced_reason",
                    trace.unsupported_steps[0]
                    if trace.unsupported_steps
                    else "the source columns are not named in the query",
                )


def _add_report_cards(builder, graph, model_card, field_of, link) -> None:
    """A card per report, carrying the model fields that report consumes."""
    for edge in graph.edges:
        consumer = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if consumer is None or target is None or consumer.kind not in ("visual", "page", "report"):
            continue
        if target.kind not in ("column", "measure", "hierarchy", "calc_item"):
            continue  # report→page and page→visual are structure, not field usage
        report_node = _report_of(graph, consumer)
        if report_node is None:
            continue
        card = builder.card(f"report:{report_node.id}", "report", report_node.name, "report")
        where = _where_label(graph, consumer, edge.kind)
        name = target.name
        if target.kind == "column" and "[" in name:
            name = name.split("[", 1)[1].rstrip("]")
        report_field = builder.field(
            card,
            name,
            "measure" if target.kind == "measure" else "field",
            table=target.meta.get("table", ""),
            detail=where,
        )
        upstream = field_of.get(edge.target)
        if upstream is None and target.kind == "measure":
            # a report-level measure lives on the report, not in the model
            upstream = builder.field(card, target.name, "report measure", detail="report-level measure")
            field_of[edge.target] = upstream
        if upstream:
            link(upstream, report_field, edge.kind, edge.evidence or where)


def _report_of(graph, node):
    seen = 0
    while node is not None and node.kind != "report" and seen < 5:
        node = graph.nodes.get(node.parent) if node.parent else None
        seen += 1
    return node if node is not None and node.kind == "report" else None


def _where_label(graph, consumer, kind: str) -> str:
    label = _CONSUMER_LABELS.get(kind, "used by")
    if consumer.kind != "visual":
        return f"{label} ({consumer.kind}-level filter)"
    page = graph.nodes.get(consumer.parent) if consumer.parent else None
    visual = consumer.meta.get("visual_type") or consumer.name
    return f"{label} — {visual}" + (f" on {page.name}" if page else "")


def _add_model_internal_links(builder, graph, field_of) -> None:
    """Measure → column hops inside the model card. They are not drawn
    between cards, but a highlighted path has to run through them or the
    chain "source column → measure → visual" breaks in the middle."""
    for edge in graph.edges:
        consumer = graph.nodes.get(edge.source)
        if consumer is None or consumer.kind not in ("measure", "column", "hierarchy", "calc_item"):
            continue
        source_field, target_field = field_of.get(edge.target), field_of.get(edge.source)
        if source_field and target_field and _card_of(source_field) == _card_of(target_field):
            builder.link(source_field, target_field, edge.kind, edge.evidence)


# ---------------------------------------------------------------------------
# Tenant graph: data source → dataflow (Gen1/Gen2) → semantic model →
# chained semantic model → report (thin or thick) / paginated / notebook
# ---------------------------------------------------------------------------


def _dataflow_generation(dataflow: dict) -> str:
    """What the scan states, not what we would like it to say.

    The Scanner marks a Fabric Dataflow Gen2 with `generation: 2`. Anything
    else is a Power BI (Gen1) dataflow. When the field is missing entirely
    the generation is unknown, and saying "Gen1" would be a guess.
    """
    generation = dataflow.get("generation")
    if generation is None:
        return ""
    return "Gen2" if int(generation) == 2 else "Gen1"


def _report_binding(report: dict, workspace_id: str, dataset_workspace: str, sibling_count: int) -> str:
    """Thin or thick, said only as far as the scan actually supports.

    A report whose model lives in another workspace is thin beyond doubt.
    A model serving several reports is being used as a shared model, so
    those reports are thin. A model with exactly one report cannot be told
    apart from a thick publish by the scan alone — so it is not claimed.
    """
    if dataset_workspace and dataset_workspace != workspace_id:
        return "thin report — model in another workspace"
    if sibling_count > 1:
        return "thin report — shared model"
    return "report — thin or thick not stated by the scan"


def tenant_lineage_graph(scan: dict, *, infer_names: bool = False) -> dict:
    """The whole estate as one canvas.

    Every edge here comes from lineage the Scanner payload *declares* —
    `datasourceUsages`, `upstreamDataflows`, `upstreamDatasets`,
    `report.datasetId`. Where an edge joins two artifacts rather than two
    columns, it is emitted at artifact grain and the canvas draws it
    differently: the scan states which model reads which dataflow, it does
    not state which column came from which entity attribute.

    `infer_names=True` additionally joins a dataflow entity attribute to a
    model column of the same name. That is an inference, never evidence,
    and every such edge says so in its own evidence string.
    """
    builder = _GraphBuilder()
    workspaces = scan.get("workspaces", []) or []

    datasource_by_id = {
        str(d.get("datasourceId") or d.get("datasourceInstanceId") or ""): d
        for d in (scan.get("datasourceInstances", []) or [])
    }
    # dataset id -> (card id, workspace id); needed for chained models and reports
    dataset_card: dict[str, str] = {}
    dataset_workspace: dict[str, str] = {}
    report_count: dict[str, int] = {}
    for workspace in workspaces:
        for dataset in workspace.get("datasets", []) or []:
            dataset_workspace[str(dataset.get("id", ""))] = str(workspace.get("id", ""))
        for report in workspace.get("reports", []) or []:
            report_count[str(report.get("datasetId", ""))] = (
                report_count.get(str(report.get("datasetId", "")), 0) + 1
            )

    def source_card(datasource_id: str) -> dict | None:
        raw = datasource_by_id.get(str(datasource_id))
        if raw is None:
            return None
        details = raw.get("connectionDetails", {}) or {}
        # Two databases on one server are two sources; naming both after the
        # server alone would collapse them into one card on the canvas.
        label = details.get("database") or details.get("path") or details.get("server") \
            or raw.get("datasourceType", "source")
        card = builder.card(
            f"src:{datasource_id}",
            "source",
            str(label).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or str(label),
            f"{raw.get('datasourceType', 'unknown')} source",
            " · ".join(str(v) for v in (details.get("server"), details.get("path")) if v),
        )
        card.setdefault("untraced", 0)
        card.setdefault(
            "untraced_reason",
            "a tenant scan does not expose source columns — the column grain "
            "comes from each model's own Power Query",
        )
        return card

    # -- dataflows ---------------------------------------------------------
    dataflow_card: dict[str, str] = {}
    for workspace in workspaces:
        workspace_name = workspace.get("name", "")
        for dataflow in workspace.get("dataflows", []) or []:
            dataflow_id = str(dataflow.get("objectId") or dataflow.get("id", ""))
            generation = _dataflow_generation(dataflow)
            card = builder.card(
                f"df:{dataflow_id}",
                "dataflow",
                dataflow.get("name", ""),
                f"dataflow {generation}".strip() if generation else "dataflow (generation not stated)",
                " · ".join(p for p in (workspace_name, dataflow.get("configuredBy", "")) if p),
            )
            card["generation"] = generation
            dataflow_card[dataflow_id] = card["id"]
            usages = dataflow.get("datasourceUsages", []) or []
            upstream_cards = [c for c in (source_card(u.get("datasourceInstanceId", "")) for u in usages) if c]
            for upstream in upstream_cards:
                builder.link(
                    upstream["id"], card["id"], "feeds",
                    "datasourceUsages on the dataflow, as reported by the scan",
                )
            for entity in dataflow.get("entities", []) or []:
                entity_name = entity.get("name", "")
                attributes = entity.get("attributes", []) or []
                if attributes:
                    for attribute in attributes:
                        builder.field(card, attribute.get("name", ""), "column", table=entity_name)
                else:
                    builder.field(card, entity_name, "entity")
                _trace_dataflow_entity(builder, card, entity, entity_name, upstream_cards)

    # -- semantic models ---------------------------------------------------
    for workspace in workspaces:
        workspace_name = workspace.get("name", "")
        for dataset in workspace.get("datasets", []) or []:
            dataset_id = str(dataset.get("id", ""))
            card = builder.card(
                f"ds:{dataset_id}",
                "semantic_model",
                dataset.get("name", ""),
                "semantic model",
                " · ".join(
                    p for p in (workspace_name, dataset.get("targetStorageMode", ""),
                                dataset.get("configuredBy", "")) if p
                ),
            )
            dataset_card[dataset_id] = card["id"]
            tables = dataset.get("tables", []) or []
            for table in tables:
                for column in table.get("columns", []) or []:
                    builder.field(card, column.get("name", ""), "column", table=table.get("name", ""))
                for measure in table.get("measures", []) or []:
                    builder.field(card, measure.get("name", ""), "measure", table=table.get("name", ""))
            if not tables:
                card["untraced"] = 0
                card["untraced_reason"] = (
                    "this scan carries no table detail — re-scan with "
                    "datasetSchema=True&datasetExpressions=True for the column grain"
                )
            for usage in dataset.get("datasourceUsages", []) or []:
                upstream = source_card(usage.get("datasourceInstanceId", ""))
                if upstream is not None:
                    builder.link(
                        upstream["id"], card["id"], "feeds",
                        "datasourceUsages on the model, as reported by the scan",
                    )
            for flow in dataset.get("upstreamDataflows", []) or []:
                upstream_id = dataflow_card.get(str(flow.get("targetDataflowId", "")))
                if upstream_id:
                    builder.link(
                        upstream_id, card["id"], "feeds",
                        "upstreamDataflows on the model, as reported by the scan",
                    )

    # -- chained models, reports and notebooks -----------------------------
    for workspace in workspaces:
        workspace_id, workspace_name = str(workspace.get("id", "")), workspace.get("name", "")
        for dataset in workspace.get("datasets", []) or []:
            consumer = dataset_card.get(str(dataset.get("id", "")))
            for upstream in dataset.get("upstreamDatasets", []) or []:
                producer = dataset_card.get(str(upstream.get("targetDatasetId", "")))
                if producer and consumer:
                    builder.link(
                        producer, consumer, "chained",
                        "upstreamDatasets — a composite model or DirectQuery over this model",
                    )
        for report in workspace.get("reports", []) or []:
            dataset_id = str(report.get("datasetId", ""))
            paginated = (report.get("reportType") or "").lower() == "paginatedreport"
            binding = _report_binding(
                report, workspace_id,
                str(report.get("datasetWorkspaceId") or dataset_workspace.get(dataset_id, "")),
                report_count.get(dataset_id, 0),
            )
            card = builder.card(
                f"rep:{report.get('id','')}",
                "paginated" if paginated else "report",
                report.get("name", ""),
                "paginated report" if paginated else binding.split(" — ")[0],
                " · ".join(p for p in (workspace_name, binding.split(" — ")[-1]) if p),
            )
            card["binding"] = binding
            card["untraced_reason"] = (
                "a tenant scan does not carry a report's visuals — open this "
                "report as a file to get its field-level usage"
            )
            producer = dataset_card.get(dataset_id)
            if producer:
                builder.link(producer, card["id"], "reads", f"report.datasetId — {binding}")
        for notebook in workspace.get("notebooks", []) or []:
            card = builder.card(
                f"nb:{notebook.get('id','')}", "notebook", notebook.get("name", ""),
                "notebook", workspace_name,
            )
            card["untraced_reason"] = "notebook code is not parsed for column usage"
            for upstream in notebook.get("upstreamDatasets", []) or []:
                producer = dataset_card.get(str(upstream.get("targetDatasetId", "")))
                if producer:
                    builder.link(producer, card["id"], "reads", "upstreamDatasets on the notebook")

    if infer_names:
        _link_matching_names(builder)
    return builder.result()


def _trace_dataflow_entity(builder, card, entity, entity_name, upstream_cards) -> None:
    """A dataflow entity's own M, when the scan carries it, gives real
    column grain on the leg the scan otherwise reports only at artifact
    level: warehouse column → entity attribute.

    It is attributed only when the dataflow reads exactly one data source.
    With two, which source a column came from is a guess, and the leg stays
    at artifact grain rather than being invented.
    """
    from pbi_lineage.mtrace import trace_table  # noqa: PLC0415 — keeps the import graph flat

    code = entity.get("mashupExpression") or entity.get("expression") or ""
    if not code or len(upstream_cards) != 1:
        return
    upstream = upstream_cards[0]
    trace = trace_table(code, entity_name)
    source_table = (trace.source_table or entity_name).split(".")[-1]
    for column_name, origin in trace.columns.items():
        if not origin.source_column:
            upstream["untraced"] = upstream.get("untraced", 0) + 1
            continue
        source_field = builder.field(upstream, origin.source_column, "column", table=source_table)
        target = builder.field(card, column_name, "column", table=entity_name)
        builder.link(
            source_field, target, origin.derivation,
            origin.detail or f"traced through the dataflow's own Power Query ({len(origin.steps)} step(s))",
        )


def _link_matching_names(builder: _GraphBuilder) -> None:
    """Join a dataflow entity attribute to a model column of the same name.

    This is an inference and is labelled as one on every edge it creates.
    It exists because a tenant scan states artifact lineage only, and a
    name match is often the answer — but a name match is not evidence, so
    it is opt-in and never silent.
    """
    linked = {(_card_of(e["source"]), _card_of(e["target"])) for e in builder.edges}
    by_name: dict[str, list[tuple[dict, dict]]] = {}
    for card in builder.cards.values():
        for field in card["fields"]:
            by_name.setdefault(field["name"].lower(), []).append((card, field))
    for candidates in by_name.values():
        for producer_card, producer_field in candidates:
            if producer_card["kind"] != "dataflow":
                continue
            for consumer_card, consumer_field in candidates:
                if consumer_card["kind"] != "semantic_model":
                    continue
                if (producer_card["id"], consumer_card["id"]) not in linked:
                    continue  # only where the scan already says the artifacts are joined
                builder.link(
                    producer_field["id"], consumer_field["id"], "inferred",
                    "inferred from a matching column name — the scan does not state column lineage",
                )
