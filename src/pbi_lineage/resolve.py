"""Reference resolver (build spec Milestones 3–4).

Builds the dependency graph from a Model, the report layer(s), and —
when a live connection provided them — the engine's own
DISCOVER_CALC_DEPENDENCY rows, then computes a usage verdict per model
object.

Verdict rules the spec mandates:

- never a binary used/unused — the status taxonomy is
  `Used` / `Unused` / `Unused (remove manually)` /
  `Indeterminate (dynamic reference)` /
  `Indeterminate (incomplete report coverage)` /
  `Not analyzable (external consumer)`;
- CALC_DEPENDENCY is the primary intra-model source; the DAX parser
  supplements (report-level measures) and cross-checks (disagreements are
  logged, never silently overridden);
- report-level measures are merged into the graph *before* resolution;
- model-internal never-unused categories (§5.3) are protected with a
  recorded reason;
- objects with statuses other than Used/Unused are excluded from any bulk
  delete by default (removal module enforces).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pbi_lineage import dax
from pbi_lineage.graph import (
    ALL_MEASURES,
    DependencyGraph,
    Node,
    nid_calc_item,
    nid_column,
    nid_expression,
    nid_hierarchy,
    nid_measure,
    nid_page,
    nid_partition,
    nid_relationship,
    nid_report,
    nid_role,
    nid_table,
    nid_visual,
)
from pbi_lineage.schema import FieldReference, Model, RefKind, ReportLayout

STATUS_USED = "Used"
STATUS_UNUSED = "Unused"
STATUS_REMOVE_MANUALLY = "Unused (remove manually)"
STATUS_DYNAMIC = "Indeterminate (dynamic reference)"
STATUS_INCOMPLETE = "Indeterminate (incomplete report coverage)"
STATUS_EXTERNAL = "Not analyzable (external consumer)"

_DELETABLE_STATUSES = {STATUS_UNUSED}


@dataclass
class UsageVerdict:
    object_id: str
    kind: str
    table: str | None
    name: str
    status: str
    is_hidden: bool = False
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @property
    def deletable(self) -> bool:
        return self.status in _DELETABLE_STATUSES


@dataclass
class Disagreement:
    """Engine (CALC_DEPENDENCY) vs parser cross-check divergence (§13)."""

    object_id: str
    detail: str


@dataclass
class AnalysisResult:
    graph: DependencyGraph
    verdicts: dict[str, UsageVerdict]
    disagreements: list[Disagreement] = field(default_factory=list)
    unresolved: list[FieldReference] = field(default_factory=list)  # broken visual refs etc.
    warnings: list[str] = field(default_factory=list)

    def verdict_for(self, object_id: str) -> UsageVerdict | None:
        return self.verdicts.get(object_id)

    def by_status(self, status: str) -> list[UsageVerdict]:
        return [v for v in self.verdicts.values() if v.status == status]


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def _add_model_nodes(graph: DependencyGraph, model: Model) -> None:
    for table in model.tables:
        graph.add_node(Node(nid_table(table.name), "table", table.name, meta={"is_hidden": table.is_hidden}))
        for column in table.columns:
            node = graph.add_node(
                Node(
                    nid_column(table.name, column.name),
                    "column",
                    f"{table.name}[{column.name}]",
                    parent=nid_table(table.name),
                    meta={"is_hidden": column.is_hidden},
                )
            )
            node.meta.setdefault("table", table.name)
            # a column belongs to its table: using the column uses the table
            graph.add_edge(node.id, nid_table(table.name), "defines", "declared", "column membership")
        for measure in table.measures:
            graph.add_node(
                Node(
                    nid_measure(measure.name),
                    "measure",
                    measure.name,
                    parent=nid_table(table.name),
                    meta={"is_hidden": measure.is_hidden, "table": table.name},
                )
            )
        for hierarchy in table.hierarchies:
            hid = nid_hierarchy(table.name, hierarchy.name)
            graph.add_node(Node(hid, "hierarchy", hierarchy.name, parent=nid_table(table.name)))
            for level in hierarchy.levels:
                if level.column:
                    graph.add_edge(
                        hid,
                        nid_column(table.name, level.column),
                        "defines",
                        "declared",
                        f"hierarchy level {level.name}",
                    )
        for partition in table.partitions:
            graph.add_node(
                Node(
                    nid_partition(table.name, partition.name),
                    "partition",
                    partition.name,
                    parent=nid_table(table.name),
                )
            )
        if table.calculation_group is not None:
            for item in table.calculation_group.items:
                graph.add_node(
                    Node(
                        nid_calc_item(table.name, item.name),
                        "calc_item",
                        item.name,
                        parent=nid_table(table.name),
                    )
                )
    for expression in model.expressions:
        graph.add_node(Node(nid_expression(expression.name), "expression", expression.name))


def _add_model_internal_edges(graph: DependencyGraph, model: Model) -> None:
    """Declared (non-DAX) edges + roots: relationships, RLS roles, sort-by."""
    for relationship in model.relationships:
        rid = nid_relationship(relationship.name)
        graph.add_node(Node(rid, "relationship", relationship.name))
        graph.mark_root(rid, "relationship")
        for table, column in (
            (relationship.from_table, relationship.from_column),
            (relationship.to_table, relationship.to_column),
        ):
            if table and column:
                state = "active" if relationship.is_active else "inactive"
                graph.add_edge(rid, nid_column(table, column), "relates", "declared", f"{state} relationship")
    for role in model.roles:
        rid = nid_role(role.name)
        graph.add_node(Node(rid, "role", role.name))
        graph.mark_root(rid, "rls-role")
    for table in model.tables:
        for column in table.columns:
            if column.sort_by_column:
                graph.add_edge(
                    nid_column(table.name, column.name),
                    nid_column(table.name, column.sort_by_column),
                    "sorts",
                    "declared",
                    f"sortByColumn of {table.name}[{column.name}]",
                )


_CALC_DEP_TYPE_TO_NODE = {
    "MEASURE": "measure",
    "CALC_COLUMN": "column",
    "COLUMN": "column",
    "ATTRIBUTE_HIERARCHY": "column",
    "CALC_TABLE": "table",
    "TABLE": "table",
    "HIERARCHY": "hierarchy",
    "PARTITION": "partition",
    "M_EXPRESSION": "expression",
    "CALCULATION_ITEM": "calc_item",
}


def _calc_dep_node_id(object_type: str, table: str | None, name: str) -> str | None:
    kind = _CALC_DEP_TYPE_TO_NODE.get(object_type.upper())
    if kind == "measure":
        return nid_measure(name)
    if kind == "column":
        return nid_column(table or "?", name)
    if kind == "table":
        return nid_table(name)
    if kind == "hierarchy":
        return nid_hierarchy(table or "?", name)
    if kind == "partition":
        return nid_partition(table or "?", name)
    if kind == "expression":
        return nid_expression(name)
    if kind == "calc_item":
        return nid_calc_item(table or "?", name)
    return None


def _add_engine_edges(graph: DependencyGraph, calc_deps, result: AnalysisResult) -> set[tuple[str, str]]:
    """DISCOVER_CALC_DEPENDENCY rows → edges with confidence "engine"."""
    engine_pairs: set[tuple[str, str]] = set()
    for dep in calc_deps:
        object_type = dep.object_type.upper()
        if object_type == "ROWS_ALLOWED":
            # RLS filter: role permission consumes the referenced object
            source = nid_role(dep.object)
            graph.add_node(Node(source, "role", dep.object))
            graph.mark_root(source, "rls-role")
        else:
            source = _calc_dep_node_id(object_type, dep.table, dep.object)
        target = _calc_dep_node_id(dep.referenced_object_type, dep.referenced_table, dep.referenced_object)
        if source is None or target is None:
            result.warnings.append(
                f"CALC_DEPENDENCY row skipped: {dep.object_type} -> {dep.referenced_object_type}"
            )
            continue
        if source not in graph.nodes:
            graph.add_node(Node(source, source.split(":", 1)[0], dep.object))
        if target not in graph.nodes:
            graph.add_node(Node(target, target.split(":", 1)[0], dep.referenced_object))
        graph.add_edge(source, target, "defines", "engine", "DISCOVER_CALC_DEPENDENCY")
        engine_pairs.add((source, target))
    return engine_pairs


def _parse_model_dax(
    graph: DependencyGraph, model: Model, result: AnalysisResult, engine_pairs: set[tuple[str, str]]
) -> None:
    """Parse every DAX expression in the model. With engine edges present
    this is a cross-check (disagreements logged); without them it is the
    primary source (confidence "parsed")."""
    have_engine = bool(engine_pairs)

    def wire(source_id: str, expression: str, host_table: str | None, what: str) -> None:
        if not expression:
            return
        analysis = dax.analyze(expression)
        resolution = dax.resolve_refs(analysis, model, host_table=host_table)
        parsed_targets: set[str] = set()
        for ref in resolution.refs:
            if ref.kind == "measure":
                target = nid_measure(ref.name)
            elif ref.kind == "column":
                target = nid_column(ref.table, ref.name)
            else:
                target = nid_table(ref.table)
            parsed_targets.add(target)
            if not have_engine:
                graph.add_edge(source_id, target, "defines", "parsed", what)
        if resolution.wildcard_measures:
            graph.add_node(Node(ALL_MEASURES, "measure", "*"))
            graph.add_edge(source_id, ALL_MEASURES, "wildcard", "wildcard", f"{what}: SELECTEDMEASURE()")
        if have_engine:
            engine_targets = {t for (s, t) in engine_pairs if s == source_id}
            # tables reached via columns/measures are implied — ignore for diff
            missing = {t for t in parsed_targets if t not in engine_targets and not t.startswith("table:")}
            extra = {
                t
                for t in engine_targets
                if t not in parsed_targets and not t.startswith(("table:", "partition:", "expression:"))
            }
            if missing:
                result.disagreements.append(
                    Disagreement(source_id, f"parser found {sorted(missing)} not in CALC_DEPENDENCY ({what})")
                )
            if extra:
                result.disagreements.append(
                    Disagreement(source_id, f"CALC_DEPENDENCY has {sorted(extra)} the parser missed ({what})")
                )

    for table in model.tables:
        for measure in table.measures:
            source = nid_measure(measure.name)
            wire(source, measure.expression, None, f"measure {measure.name}")
            if measure.format_string_expression:
                wire(source, measure.format_string_expression, None, f"dynamic format of {measure.name}")
        for column in table.columns:
            if column.expression:
                wire(
                    nid_column(table.name, column.name),
                    column.expression,
                    table.name,
                    f"calculated column {table.name}[{column.name}]",
                )
        for partition in table.partitions:
            if partition.source_type == "calculated" and partition.expression:
                wire(
                    nid_table(table.name),
                    partition.expression,
                    None,
                    f"calculated table {table.name}",
                )
        if table.calculation_group is not None:
            for item in table.calculation_group.items:
                source = nid_calc_item(table.name, item.name)
                wire(source, item.expression, None, f"calculation item {item.name}")
                if item.format_string_expression:
                    wire(source, item.format_string_expression, None, f"format of calc item {item.name}")
    for role in model.roles:
        for table_name, filter_dax in role.table_permissions.items():
            wire(nid_role(role.name), filter_dax, table_name, f"RLS filter on {table_name}")


def _add_report_level_measures(
    graph: DependencyGraph, model: Model, reports: list[ReportLayout], result: AnalysisResult
) -> dict[str, str]:
    """§5.4.2: parse report-level measures BEFORE resolving visual refs —
    order matters. Returns measure name → node id for reference lookup."""
    rl_index: dict[str, str] = {}
    for report in reports:
        report_name = report.name or "report"
        for rl in report.report_level_measures:
            node_id = nid_measure(rl.name, host_report=report_name)
            graph.add_node(
                Node(
                    node_id,
                    "measure",
                    rl.name,
                    parent=nid_report(report_name),
                    meta={"is_report_level": True, "host_report": report_name, "table": rl.table},
                )
            )
            rl_index.setdefault(rl.name, node_id)
            analysis = dax.analyze(rl.expression)
            resolution = dax.resolve_refs(analysis, model, host_table=rl.table)
            for ref in resolution.refs:
                if ref.kind == "measure":
                    target = nid_measure(ref.name)
                elif ref.kind == "column":
                    target = nid_column(ref.table, ref.name)
                else:
                    target = nid_table(ref.table)
                graph.add_edge(node_id, target, "defines", "parsed", f"report-level measure {rl.name}")
            if resolution.wildcard_measures:
                graph.add_node(Node(ALL_MEASURES, "measure", "*"))
                graph.add_edge(
                    node_id, ALL_MEASURES, "wildcard", "wildcard", f"report-level measure {rl.name}"
                )
    return rl_index


_SCOPE_EDGE_KIND = {
    "visual:projection": "projects",
    "visual:sort": "sorts",
    "visual:formatting": "formats",
    "visual:filter": "filters",
    "visual:other": "projects",
    "page:filter": "filters",
    "report:filter": "filters",
    "report:bookmark": "filters",
}


def _resolve_field_reference(model: Model, ref: FieldReference, rl_index: dict[str, str]) -> str | None:
    """FieldReference → target node id, or None when it points at nothing
    (a broken visual — reported, never guessed)."""
    if ref.kind == RefKind.MEASURE:
        table = model.table(ref.table) if ref.table else None
        if table is not None and any(m.name == ref.name for m in table.measures):
            return nid_measure(ref.name)
        if ref.name in rl_index:
            return rl_index[ref.name]
        for t in model.tables:
            if any(m.name == ref.name for m in t.measures):
                return nid_measure(ref.name)
        return None
    if ref.kind == RefKind.COLUMN:
        if ref.table:
            table = model.table(ref.table)
            if table is not None:
                if any(c.name == ref.name for c in table.columns):
                    return nid_column(table.name, ref.name)
                if any(m.name == ref.name for m in table.measures):
                    return nid_measure(ref.name)  # filters sometimes carry measures as Column
            if ref.name in rl_index:
                return rl_index[ref.name]
        return None
    if ref.kind == RefKind.HIERARCHY_LEVEL:
        hierarchy_name = ref.name.split(".", 1)[0]
        if ref.table and model.table(ref.table) is not None:
            table = model.table(ref.table)
            if any(h.name == hierarchy_name for h in table.hierarchies):
                return nid_hierarchy(ref.table, hierarchy_name)
        return None
    return None


def _add_report_edges(
    graph: DependencyGraph,
    model: Model,
    reports: list[ReportLayout],
    rl_index: dict[str, str],
    result: AnalysisResult,
) -> None:
    for report in reports:
        report_name = report.name or "report"
        report_id = nid_report(report_name)
        graph.add_node(Node(report_id, "report", report_name))
        graph.mark_root(report_id, "report-filter-scope")
        for ref in report.filters:
            _wire_ref(graph, model, report_id, ref, rl_index, result)
        for page in report.pages:
            page_id = nid_page(report_name, page.name)
            graph.add_node(
                Node(
                    page_id,
                    "page",
                    page.display_name or page.name,
                    parent=report_id,
                    meta={"is_hidden": page.is_hidden},
                )
            )
            graph.add_edge(report_id, page_id, "defines", "declared", "page of report")
            graph.mark_root(page_id, "page-filter-scope")
            for ref in page.filters:
                _wire_ref(graph, model, page_id, ref, rl_index, result)
            for visual in page.visuals:
                visual_id = nid_visual(report_name, page.name, visual.name or "visual")
                graph.add_node(
                    Node(
                        visual_id,
                        "visual",
                        visual.visual_type or "visual",
                        parent=page_id,
                        meta={"is_hidden": visual.is_hidden, "visual_type": visual.visual_type},
                    )
                )
                graph.add_edge(page_id, visual_id, "defines", "declared", "visual on page")
                # hidden visuals/pages are used, not unused (§5.3)
                graph.mark_root(visual_id, "visual")
                for ref in visual.references:
                    _wire_ref(graph, model, visual_id, ref, rl_index, result)


def _wire_ref(
    graph: DependencyGraph,
    model: Model,
    source_id: str,
    ref: FieldReference,
    rl_index: dict[str, str],
    result: AnalysisResult,
) -> None:
    target = _resolve_field_reference(model, ref, rl_index)
    if target is None:
        result.unresolved.append(ref)
        return
    graph.add_edge(source_id, target, _SCOPE_EDGE_KIND.get(ref.scope, "projects"), "parsed", ref.evidence)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def _protected_reasons(model: Model) -> dict[str, list[str]]:
    """§5.3 model-internal never-mark-unused categories, with reasons."""
    protected: dict[str, list[str]] = {}

    def add(node_id: str, reason: str) -> None:
        protected.setdefault(node_id, []).append(reason)

    for relationship in model.relationships:
        for table, column in (
            (relationship.from_table, relationship.from_column),
            (relationship.to_table, relationship.to_column),
        ):
            if table and column:
                add(nid_column(table, column), f"relationship {relationship.name}")
    for table in model.tables:
        if table.calculation_group is not None:
            add(nid_table(table.name), "calculation group table")
            for item in table.calculation_group.items:
                add(nid_calc_item(table.name, item.name), "calculation group item")
        for column in table.columns:
            if column.sort_by_column:
                add(nid_column(table.name, column.sort_by_column), f"sortByColumn target of {column.name}")
            if column.is_key:
                add(nid_column(table.name, column.name), "key column")
        for hierarchy in table.hierarchies:
            for level in hierarchy.levels:
                if level.column:
                    add(nid_column(table.name, level.column), f"level of hierarchy {hierarchy.name}")
        for partition in table.partitions:
            expression = partition.expression or ""
            if "RangeStart" in expression or "RangeEnd" in expression:
                add(nid_table(table.name), "incremental refresh policy partition")
    for role in model.roles:
        for table_name, filter_dax in role.table_permissions.items():
            analysis = dax.analyze(filter_dax)
            resolution = dax.resolve_refs(analysis, model, host_table=table_name)
            for ref in resolution.refs:
                if ref.kind == "column":
                    add(nid_column(ref.table, ref.name), f"RLS filter of role {role.name}")
                elif ref.kind == "measure":
                    add(nid_measure(ref.name), f"RLS filter of role {role.name}")
    return protected


def analyze_model(
    model: Model,
    reports: list[ReportLayout] | None = None,
    calc_deps=None,
    *,
    external_consumers: list[str] | None = None,
    coverage_complete: bool = True,
) -> AnalysisResult:
    """Build the graph and produce a verdict per model object.

    `external_consumers` — non-empty means something consumes this model
    that cannot be parsed (Analyze in Excel, executeQueries, …): every
    Unused is capped to `Not analyzable (external consumer)` (§5.4.5).
    `coverage_complete=False` — not every connected report was retrieved
    and parsed: every Unused is capped to
    `Indeterminate (incomplete report coverage)` (§5.4.4 hard gate).
    """
    reports = reports or []
    external_consumers = external_consumers or []
    graph = DependencyGraph()
    result = AnalysisResult(graph=graph, verdicts={})

    _add_model_nodes(graph, model)
    _add_model_internal_edges(graph, model)
    engine_pairs = _add_engine_edges(graph, calc_deps, result) if calc_deps else set()
    _parse_model_dax(graph, model, result, engine_pairs)
    rl_index = _add_report_level_measures(graph, model, reports, result)  # before visual refs
    _add_report_edges(graph, model, reports, rl_index, result)

    reached = graph.reachable()
    protected = _protected_reasons(model)

    def verdict(node_id: str, kind: str, table: str | None, name: str, is_hidden: bool) -> UsageVerdict:
        if node_id in reached:
            edges = reached[node_id]
            return UsageVerdict(
                node_id,
                kind,
                table,
                name,
                STATUS_USED,
                is_hidden,
                reasons=[graph.roots.get(node_id, "reachable from report/RLS/relationship root")],
                evidence=[e.evidence for e in edges][:8],
            )
        if node_id in protected:
            return UsageVerdict(
                node_id, kind, table, name, STATUS_USED, is_hidden, reasons=protected[node_id][:8]
            )
        if not coverage_complete:
            return UsageVerdict(
                node_id,
                kind,
                table,
                name,
                STATUS_INCOMPLETE,
                is_hidden,
                reasons=["not every connected report could be retrieved and parsed"],
            )
        if external_consumers:
            return UsageVerdict(
                node_id,
                kind,
                table,
                name,
                STATUS_EXTERNAL,
                is_hidden,
                reasons=[f"external consumer: {c}" for c in external_consumers[:8]],
            )
        return UsageVerdict(node_id, kind, table, name, STATUS_UNUSED, is_hidden)

    for table in model.tables:
        for column in table.columns:
            node_id = nid_column(table.name, column.name)
            result.verdicts[node_id] = verdict(node_id, "column", table.name, column.name, column.is_hidden)
        for measure in table.measures:
            node_id = nid_measure(measure.name)
            result.verdicts[node_id] = verdict(
                node_id, "measure", table.name, measure.name, measure.is_hidden
            )
        for hierarchy in table.hierarchies:
            node_id = nid_hierarchy(table.name, hierarchy.name)
            result.verdicts[node_id] = verdict(
                node_id, "hierarchy", table.name, hierarchy.name, hierarchy.is_hidden
            )
        if table.calculation_group is not None:
            for item in table.calculation_group.items:
                node_id = nid_calc_item(table.name, item.name)
                result.verdicts[node_id] = verdict(node_id, "calc_item", table.name, item.name, False)

    # table verdict: used iff the table node is reached/protected or any member is Used
    for table in model.tables:
        node_id = nid_table(table.name)
        table_verdict = verdict(node_id, "table", None, table.name, table.is_hidden)
        if table_verdict.status != STATUS_USED:
            member_used = any(
                result.verdicts[m].status == STATUS_USED
                for m in (
                    [nid_column(table.name, c.name) for c in table.columns]
                    + [nid_measure(m.name) for m in table.measures]
                )
                if m in result.verdicts
            )
            if member_used:
                table_verdict = UsageVerdict(
                    node_id,
                    "table",
                    None,
                    table.name,
                    STATUS_USED,
                    table.is_hidden,
                    reasons=["member in use"],
                )
        result.verdicts[node_id] = table_verdict

    # report-level measures: usable verdicts too — but never deletable via script
    for name, node_id in rl_index.items():
        node = graph.nodes[node_id]
        status = STATUS_USED if node_id in reached else STATUS_REMOVE_MANUALLY
        result.verdicts[node_id] = UsageVerdict(
            node_id,
            "measure",
            node.meta.get("table"),
            name,
            status,
            reasons=(
                ["reachable"]
                if node_id in reached
                else ["report-level measure: lives in the report file, not the model"]
            ),
        )

    # any expression flagged dynamic (wildcard) that is otherwise Unused → Indeterminate
    for edge in graph.edges:
        if edge.kind == "wildcard":
            v = result.verdicts.get(edge.source)
            if v is not None and v.status == STATUS_UNUSED:
                v.status = STATUS_DYNAMIC
                v.reasons.append("contains dynamic reference (SELECTEDMEASURE family)")

    return result
