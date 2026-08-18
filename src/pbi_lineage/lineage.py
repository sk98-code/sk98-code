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
