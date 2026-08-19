"""Normalized internal schema (build spec Section 8, file-reader subset).

Everything a reader produces lands in these dataclasses so the analysis
core never knows whether data came from a PBIX zip, a PBIP project, or
(later) a live AS instance / the Scanner API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RefKind(str, Enum):
    """What kind of model object a report reference points at."""

    COLUMN = "column"
    MEASURE = "measure"
    HIERARCHY_LEVEL = "hierarchy_level"


@dataclass(frozen=True)
class FieldReference:
    """One place in a report where a model field is consumed.

    `evidence` is the JSON-path-ish location the reference was found at —
    the spec forbids verdicts without evidence, so it is mandatory here.
    `table` is None when the source alias could not be resolved (the
    resolver must treat that as indeterminate, never as unused).
    """

    table: str | None
    name: str
    kind: RefKind
    scope: str  # e.g. "visual:projection", "visual:filter", "page:filter"
    evidence: str

    def key(self) -> tuple[str | None, str, RefKind]:
        return (self.table, self.name, self.kind)


# ---------------------------------------------------------------------------
# Model side
# ---------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    data_type: str | None = None
    is_hidden: bool = False
    is_key: bool = False
    is_calculated: bool = False
    source_column: str | None = None
    sort_by_column: str | None = None
    summarize_by: str | None = None
    format_string: str | None = None
    data_category: str | None = None
    expression: str | None = None  # calculated columns only


@dataclass
class Measure:
    name: str
    expression: str = ""
    format_string: str | None = None
    # dynamic format string DAX (§5.3: its references are never unused)
    format_string_expression: str | None = None
    display_folder: str | None = None
    is_hidden: bool = False
    description: str | None = None
    # Report-level measures live in the report file, not the model
    # (spec §5.4.2): they can never be deleted through TOM/XMLA.
    is_report_level: bool = False
    host_report: str | None = None


@dataclass
class HierarchyLevel:
    name: str
    column: str | None = None


@dataclass
class Hierarchy:
    name: str
    levels: list[HierarchyLevel] = field(default_factory=list)
    is_hidden: bool = False


@dataclass
class Partition:
    name: str
    source_type: str | None = None  # m | calculated | entity | query
    mode: str | None = None  # import | directQuery | dual
    expression: str | None = None


@dataclass
class CalculationItem:
    """One item of a calculation group. `SELECTEDMEASURE()` inside makes it
    depend on *every* measure — a wildcard edge, never zero edges (§5.3)."""

    name: str
    expression: str = ""
    ordinal: int | None = None
    format_string_expression: str | None = None


@dataclass
class CalculationGroup:
    precedence: int | None = None
    items: list[CalculationItem] = field(default_factory=list)


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    hierarchies: list[Hierarchy] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    is_hidden: bool = False
    data_category: str | None = None
    calculation_group: CalculationGroup | None = None

    @property
    def is_calculated(self) -> bool:
        return any(p.source_type == "calculated" for p in self.partitions)


@dataclass
class Relationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool = True
    to_cardinality: str | None = None  # one | many (None = default manyToOne)
    cross_filtering: str | None = None


@dataclass
class Role:
    """RLS role — its filter DAX makes columns 'used' (spec §5.3)."""

    name: str
    model_permission: str | None = None
    table_permissions: dict[str, str] = field(default_factory=dict)  # table -> DAX


@dataclass
class NamedExpression:
    """Shared M expression / parameter (TMSCHEMA_EXPRESSIONS analogue)."""

    name: str
    expression: str = ""


@dataclass
class Model:
    name: str
    culture: str | None = None
    tables: list[Table] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)
    expressions: list[NamedExpression] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)


@dataclass(frozen=True)
class CalcDependency:
    """One row of DISCOVER_CALC_DEPENDENCY — the engine's own resolution of
    intra-model dependencies (spec §4.4), the *primary* dependency source."""

    object_type: str
    table: str | None
    object: str
    expression: str | None
    referenced_object_type: str
    referenced_table: str | None
    referenced_object: str


# ---------------------------------------------------------------------------
# Report side
# ---------------------------------------------------------------------------


@dataclass
class Visual:
    name: str  # stable id from the report file
    visual_type: str | None = None
    # The title the report author typed, when it is a literal. A dynamic
    # title is an expression, not a name, so it is left out rather than
    # rendered as DAX in a sentence.
    title: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    is_hidden: bool = False
    sync_group: str | None = None
    references: list[FieldReference] = field(default_factory=list)

    def fields(self) -> list[tuple[str | None, str, RefKind]]:
        """Unique (table, name, kind) tuples referenced by this visual."""
        seen: dict[tuple[str | None, str, RefKind], None] = {}
        for ref in self.references:
            seen.setdefault(ref.key())
        return list(seen)


@dataclass
class Page:
    name: str
    display_name: str | None = None
    ordinal: int | None = None
    is_hidden: bool = False
    visuals: list[Visual] = field(default_factory=list)
    filters: list[FieldReference] = field(default_factory=list)


@dataclass
class ReportLevelMeasure:
    """DAX defined inside the report file (spec §5.4.2). Invisible to the
    model; its expression is stored verbatim for the resolver (Milestone 3)."""

    name: str
    table: str | None = None
    expression: str = ""
    is_hidden: bool = False


@dataclass
class ReportLayout:
    name: str | None = None
    source_format: str = ""  # "legacy" | "pbir"
    pages: list[Page] = field(default_factory=list)
    filters: list[FieldReference] = field(default_factory=list)  # report-level
    report_level_measures: list[ReportLevelMeasure] = field(default_factory=list)

    def all_references(self) -> list[FieldReference]:
        out = list(self.filters)
        for page in self.pages:
            out.extend(page.filters)
            for visual in page.visuals:
                out.extend(visual.references)
        return out


# ---------------------------------------------------------------------------
# Reader result container
# ---------------------------------------------------------------------------


@dataclass
class LiveConnection:
    """Remote-model connection of a thin report (spec §5.4.1)."""

    dataset_id: str | None = None
    connection_string: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ReadResult:
    source_path: str
    source_format: str  # "pbix" | "pbip"
    model: Model | None = None
    report: ReportLayout | None = None
    is_thin: bool = False
    connection: LiveConnection | None = None
    m_section: str | None = None  # DataMashup Formulas/Section1.m when present
    warnings: list[str] = field(default_factory=list)
