"""The property-graph model and the normalised tenant snapshot that feeds it.

Two layers live here:

* *Snapshot* models — whatever the Scanner API / XMLA / PBIX layout gave us,
  normalised so the graph builder never has to care which path produced it.
* *Graph* models — the nodes and edges that land in Neo4j.

Node identity is deterministic (see `node_id`) so re-scanning a workspace
MERGEs onto the same nodes instead of duplicating them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# graph vocabulary
# --------------------------------------------------------------------------
class NodeKind(StrEnum):
    WORKSPACE = "Workspace"
    DATA_SOURCE = "DataSource"
    DATAFLOW = "Dataflow"
    SEMANTIC_MODEL = "SemanticModel"
    TABLE = "Table"
    COLUMN = "Column"
    MEASURE = "Measure"
    REPORT = "Report"
    PAGE = "Page"
    VISUAL = "Visual"


class EdgeKind(StrEnum):
    #: derived object -> the thing it is computed from (points upstream)
    DERIVES_FROM = "derives_from"
    #: producer -> the place it is consumed (points downstream)
    USED_IN = "used_in"
    #: structural containment, not data flow (workspace -> model -> table -> column)
    CONTAINS = "contains"


class Confidence(StrEnum):
    #: the engine resolved it for us (DMV) or it is a literal binding in metadata
    RESOLVED = "resolved"
    #: our tokenizer recognised the construct and inferred the reference
    HEURISTIC = "heuristic"
    #: we saw something we will not guess at; the edge records the gap
    OPAQUE = "opaque"


CONFIDENCE_RANK = {Confidence.OPAQUE: 0, Confidence.HEURISTIC: 1, Confidence.RESOLVED: 2}

#: edges that represent data flow (as opposed to containment)
LINEAGE_EDGES = (EdgeKind.DERIVES_FROM, EdgeKind.USED_IN)


def _slug(value: str) -> str:
    return (value or "").strip().lower()


def node_id(kind: NodeKind, *parts: str) -> str:
    """Deterministic node key. Case-folded so 'Sales'[Amount] == 'sales'[amount]."""
    body = "|".join(_slug(p) for p in parts if p is not None)
    return f"{kind.value}:{body}"


class LineageNode(BaseModel):
    id: str
    kind: NodeKind
    name: str
    #: display path, e.g. "Finance / Sales Model / FactSales / Amount"
    qualified_name: str = ""
    workspace_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    def merge(self, other: LineageNode) -> LineageNode:
        """Later observations win for non-empty fields; properties union."""
        props = {**self.properties, **{k: v for k, v in other.properties.items() if v is not None}}
        return LineageNode(
            id=self.id,
            kind=self.kind,
            name=other.name or self.name,
            qualified_name=other.qualified_name or self.qualified_name,
            workspace_id=other.workspace_id or self.workspace_id,
            properties=props,
        )


class LineageEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    confidence: Confidence = Confidence.HEURISTIC
    #: how we know: "DISCOVER_CALC_DEPENDENCY", "dax-tokenizer", "m:Table.RenameColumns", ...
    evidence: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.kind.value)


class LineageGraph(BaseModel):
    """A whole scan result: nodes + edges + provenance about the scan itself."""

    nodes: dict[str, LineageNode] = Field(default_factory=dict)
    edges: list[LineageEdge] = Field(default_factory=list)
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = Field(default_factory=list)

    def add_node(self, node: LineageNode) -> LineageNode:
        existing = self.nodes.get(node.id)
        merged = existing.merge(node) if existing else node
        self.nodes[node.id] = merged
        return merged

    def add_edge(self, edge: LineageEdge) -> None:
        """Add, keeping the highest-confidence version of a duplicate edge."""
        for i, present in enumerate(self.edges):
            if present.key == edge.key:
                if CONFIDENCE_RANK[edge.confidence] > CONFIDENCE_RANK[present.confidence]:
                    self.edges[i] = edge
                return
        self.edges.append(edge)

    def extend(self, other: LineageGraph) -> None:
        for node in other.nodes.values():
            self.add_node(node)
        for edge in other.edges:
            self.add_edge(edge)
        self.warnings.extend(other.warnings)

    def edges_of(self, kinds: Iterable[EdgeKind] | None = None) -> list[LineageEdge]:
        if kinds is None:
            return list(self.edges)
        wanted = set(kinds)
        return [e for e in self.edges if e.kind in wanted]

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for node in self.nodes.values():
            by_kind[node.kind.value] = by_kind.get(node.kind.value, 0) + 1
        by_conf: dict[str, int] = {}
        for edge in self.edges:
            if edge.kind in LINEAGE_EDGES:
                by_conf[edge.confidence.value] = by_conf.get(edge.confidence.value, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "nodes_by_kind": dict(sorted(by_kind.items())),
            "lineage_edges_by_confidence": dict(sorted(by_conf.items())),
            "warnings": len(self.warnings),
            "scanned_at": self.scanned_at.isoformat(),
        }


# --------------------------------------------------------------------------
# tenant snapshot (normalised collector output)
# --------------------------------------------------------------------------
class CapacityTier(StrEnum):
    """Decides which dependency-resolution path a workspace gets routed to."""

    PREMIUM = "premium"  # P/EM SKU — XMLA available
    FABRIC = "fabric"  # F SKU — XMLA available
    PPU = "ppu"  # Premium per user — XMLA available
    PRO = "pro"  # shared capacity — no XMLA endpoint
    UNKNOWN = "unknown"

    @property
    def has_xmla(self) -> bool:
        return self in {CapacityTier.PREMIUM, CapacityTier.FABRIC, CapacityTier.PPU}


class ColumnSpec(BaseModel):
    name: str
    data_type: str = ""
    is_calculated: bool = False
    expression: str = ""
    is_hidden: bool = False
    description: str = ""


class MeasureSpec(BaseModel):
    name: str
    table: str
    expression: str = ""
    is_hidden: bool = False
    description: str = ""


class PartitionSpec(BaseModel):
    name: str
    #: "m" (Power Query), "calculated" (DAX), "entity" (dataflow/Direct Lake), "unknown"
    source_type: str = "unknown"
    expression: str = ""
    #: set when source_type == "entity"
    dataflow_id: str = ""
    entity_name: str = ""


class TableSpec(BaseModel):
    name: str
    columns: list[ColumnSpec] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)
    partitions: list[PartitionSpec] = Field(default_factory=list)
    is_calculated: bool = False
    description: str = ""

    def column(self, name: str) -> ColumnSpec | None:
        target = _slug(name)
        return next((c for c in self.columns if _slug(c.name) == target), None)


class DataSourceSpec(BaseModel):
    id: str = ""
    #: "Sql", "Web", "Lakehouse", "AnalysisServices", ...
    kind: str = "Unknown"
    server: str = ""
    database: str = ""
    path: str = ""

    def display(self) -> str:
        bits = [b for b in (self.server, self.database, self.path) if b]
        return " / ".join(bits) or self.kind


class DatasetSpec(BaseModel):
    """A semantic model, as seen by whichever collector produced it."""

    id: str
    name: str
    workspace_id: str
    tables: list[TableSpec] = Field(default_factory=list)
    data_sources: list[DataSourceSpec] = Field(default_factory=list)
    #: shared/model-level M expressions (parameters, shared queries)
    expressions: dict[str, str] = Field(default_factory=dict)
    configured_by: str = ""
    #: filled by the router so downstream code knows which path ran
    resolution_path: str = "unknown"

    def table(self, name: str) -> TableSpec | None:
        target = _slug(name)
        return next((t for t in self.tables if _slug(t.name) == target), None)

    def measure_names(self) -> set[str]:
        return {_slug(m.name) for t in self.tables for m in t.measures}

    def find_measure(self, name: str) -> MeasureSpec | None:
        target = _slug(name)
        for table in self.tables:
            for measure in table.measures:
                if _slug(measure.name) == target:
                    return measure
        return None


class DataflowSpec(BaseModel):
    id: str
    name: str
    workspace_id: str
    #: entity/query name -> M script
    queries: dict[str, str] = Field(default_factory=dict)
    data_sources: list[DataSourceSpec] = Field(default_factory=list)


class VisualFieldSpec(BaseModel):
    """One field binding inside a visual (or a filter, or a format rule)."""

    table: str
    field: str
    #: "measure" when we know it is one, "column" when we know it is not, else "unknown"
    field_kind: str = "unknown"
    #: "Category", "Y", "Series", "Tooltips", "filter", "conditional_formatting", ...
    role: str = "field"
    aggregation: str = ""


class VisualSpec(BaseModel):
    id: str
    visual_type: str = ""
    title: str = ""
    fields: list[VisualFieldSpec] = Field(default_factory=list)


class PageSpec(BaseModel):
    id: str
    name: str
    ordinal: int = 0
    visuals: list[VisualSpec] = Field(default_factory=list)
    #: page-level filters
    fields: list[VisualFieldSpec] = Field(default_factory=list)


class ReportSpec(BaseModel):
    id: str
    name: str
    workspace_id: str
    dataset_id: str = ""
    pages: list[PageSpec] = Field(default_factory=list)
    #: report-level filters
    fields: list[VisualFieldSpec] = Field(default_factory=list)
    #: False when the Export API had not produced a PBIX for this report yet
    layout_available: bool = False


class WorkspaceSpec(BaseModel):
    id: str
    name: str
    capacity_id: str = ""
    capacity_sku: str = ""
    tier: CapacityTier = CapacityTier.UNKNOWN
    is_personal: bool = False
    state: str = ""
    datasets: list[DatasetSpec] = Field(default_factory=list)
    reports: list[ReportSpec] = Field(default_factory=list)
    dataflows: list[DataflowSpec] = Field(default_factory=list)


class TenantSnapshot(BaseModel):
    workspaces: list[WorkspaceSpec] = Field(default_factory=list)
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = Field(default_factory=list)

    def workspace(self, workspace_id: str) -> WorkspaceSpec | None:
        return next((w for w in self.workspaces if w.id == workspace_id), None)
