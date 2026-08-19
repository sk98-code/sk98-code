"""Best-practice / health rule engine — build spec §9, Milestone 10 (C9).

Data-driven, not hardcoded checks: every rule is a `Rule` record with a
predicate over an `EstateContext`, so rules can be added, filtered by
severity, disabled per tenant, or loaded from config without touching the
engine. `run_rules` returns `Finding` rows matching the Section 8
`finding` table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from pbi_lineage.graph import nid_column, nid_measure, nid_table
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.resolve import STATUS_UNUSED, AnalysisResult
from pbi_lineage.schema import Model, ReportLayout
from pbi_lineage.service.thin_reports import field_usage_frequency, jaccard

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


@dataclass
class Finding:
    rule_id: str
    severity: str
    object_id: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class EstateContext:
    """Everything the rules can see. Optional pieces stay None so a rule
    that needs them is skipped rather than guessing."""

    model: Model
    analysis: AnalysisResult | None = None
    reports: list[ReportLayout] = field(default_factory=list)
    m_index: MExpressionIndex | None = None
    peer_models: list[Model] = field(default_factory=list)
    never_viewed_reports: list[str] = field(default_factory=list)
    days_since_refresh: int | None = None
    unused_custom_visuals: list[str] = field(default_factory=list)
    column_cardinality: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass
class Rule:
    id: str
    severity: str
    description: str
    check: Callable[[EstateContext], Iterable[Finding]]
    enabled: bool = True


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _implicit_measures(context: EstateContext) -> Iterable[Finding]:
    """Aggregations on raw columns instead of explicit measures: a column
    projected into a value bucket while its summarizeBy is not none."""
    summarize = {
        (table.name, column.name): (column.summarize_by or "").lower()
        for table in context.model.tables
        for column in table.columns
    }
    for report in context.reports:
        for page in report.pages:
            for visual in page.visuals:
                for reference in visual.references:
                    if reference.scope != "visual:projection" or reference.kind.value != "column":
                        continue
                    key = (reference.table or "", reference.name)
                    behaviour = summarize.get(key)
                    if behaviour and behaviour not in ("none", ""):
                        yield Finding(
                            "implicit-measure",
                            SEVERITY_WARNING,
                            nid_column(key[0], key[1]),
                            f"{key[0]}[{key[1]}] is aggregated implicitly ({behaviour}) in "
                            f"visual {visual.name or visual.visual_type}",
                            {"report": report.name, "page": page.name, "summarize_by": behaviour},
                        )


_AUTO_TABLE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_", "NewLocalDateTable_")


def _broken_visuals(context: EstateContext) -> Iterable[Finding]:
    """Visuals referencing a field that is not in the model.

    One finding per distinct field, not per occurrence. The same missing
    field referenced by four hundred visuals is one thing to fix, and four
    hundred rows of it buries every other finding in the report — which is
    what makes a rule engine useless rather than merely noisy.

    Power BI's own auto date/time tables are separated out: a reference to
    a `LocalDateTable_…` the extractor did not return is a limitation of
    the read, not a broken report, and telling a user to go fix it wastes
    their afternoon.
    """
    if context.analysis is None:
        return
    grouped: dict[tuple[str, str], list] = {}
    for reference in context.analysis.unresolved:
        grouped.setdefault((reference.table or "", reference.name), []).append(reference)

    for (table, name), references in sorted(grouped.items()):
        occurrences = len(references)
        scopes = sorted({r.scope for r in references})
        detail = {
            "occurrences": occurrences,
            "scopes": scopes,
            "evidence": references[0].evidence,
            "more_evidence": [r.evidence for r in references[1:4]],
        }
        where = f" in {occurrences} places" if occurrences > 1 else ""
        if table.startswith(_AUTO_TABLE_PREFIXES):
            yield Finding(
                "auto-date-reference",
                SEVERITY_INFO,
                f"{table}[{name}]",
                f"report uses the auto date/time table {table}[{name}]{where}; that table "
                "was not returned by the model reader, so this is a gap in the read, "
                "not a broken report",
                detail,
            )
            continue
        if not table:
            yield Finding(
                "unresolved-reference",
                SEVERITY_WARNING,
                f"[{name}]",
                f"report references [{name}]{where} without naming a table, and no single "
                "model object answers to that name — it may be a visual-internal field or "
                "an ambiguous name",
                detail,
            )
            continue
        yield Finding(
            "broken-visual-reference",
            SEVERITY_ERROR,
            f"{table}[{name}]",
            f"report references {table}[{name}]{where}, which is not in the model",
            detail,
        )


def _broken_dax(context: EstateContext) -> Iterable[Finding]:
    """Measure/calc column DAX referencing a deleted object."""
    from pbi_lineage import dax  # local import keeps the rule table light

    for table in context.model.tables:
        for measure in table.measures:
            resolution = dax.resolve_refs(dax.analyze(measure.expression), context.model)
            for unknown in resolution.unknown:
                yield Finding(
                    "broken-dax",
                    SEVERITY_ERROR,
                    nid_measure(measure.name),
                    f"measure [{measure.name}] references unknown " f"{unknown.table or ''}[{unknown.name}]",
                    {"table": table.name},
                )
        for column in table.columns:
            if not column.expression:
                continue
            resolution = dax.resolve_refs(
                dax.analyze(column.expression), context.model, host_table=table.name
            )
            for unknown in resolution.unknown:
                yield Finding(
                    "broken-dax",
                    SEVERITY_ERROR,
                    nid_column(table.name, column.name),
                    f"calculated column {table.name}[{column.name}] references unknown "
                    f"{unknown.table or ''}[{unknown.name}]",
                    {"table": table.name},
                )


def _auto_date_tables(context: EstateContext) -> Iterable[Finding]:
    """Auto date/time tables carry a large hidden size cost."""
    for table in context.model.tables:
        if table.name.startswith("LocalDateTable_") or table.name.startswith("DateTableTemplate_"):
            yield Finding(
                "auto-date-table",
                SEVERITY_WARNING,
                nid_table(table.name),
                f"auto date/time table {table.name} — disable Auto date/time and use a shared date table",
                {},
            )


def _bidirectional_relationships(context: EstateContext) -> Iterable[Finding]:
    for relationship in context.model.relationships:
        if (relationship.cross_filtering or "").lower() == "bothdirections":
            yield Finding(
                "bidirectional-relationship",
                SEVERITY_WARNING,
                f"relationship:{relationship.name}",
                f"relationship {relationship.name} filters both directions "
                f"({relationship.from_table} <-> {relationship.to_table})",
                {},
            )
        if (relationship.to_cardinality or "").lower() == "many":
            yield Finding(
                "many-to-many-relationship",
                SEVERITY_INFO,
                f"relationship:{relationship.name}",
                f"relationship {relationship.name} is many-to-many",
                {},
            )


def _high_cardinality_unused(context: EstateContext) -> Iterable[Finding]:
    if context.analysis is None or not context.column_cardinality:
        return
    for (table, column), cardinality in context.column_cardinality.items():
        verdict = context.analysis.verdicts.get(nid_column(table, column))
        if verdict is not None and verdict.status == STATUS_UNUSED and cardinality >= 100_000:
            yield Finding(
                "high-cardinality-unused-column",
                SEVERITY_WARNING,
                nid_column(table, column),
                f"{table}[{column}] is unused and has {cardinality:,} distinct values",
                {"cardinality": cardinality},
            )


def _duplicate_models(context: EstateContext) -> Iterable[Finding]:
    """Near-identical semantic models (Jaccard over object name sets)."""

    def signature(model: Model) -> set[str]:
        names: set[str] = set()
        for table in model.tables:
            names.add(f"t:{table.name}")
            names.update(f"c:{table.name}[{c.name}]" for c in table.columns)
            names.update(f"m:{m.name}" for m in table.measures)
        return names

    own = signature(context.model)
    for peer in context.peer_models:
        if peer.name == context.model.name:
            continue
        score = jaccard(own, signature(peer))
        if score >= 0.85:
            yield Finding(
                "duplicate-model",
                SEVERITY_WARNING,
                f"model:{context.model.name}",
                f"model '{context.model.name}' is {score:.0%} identical to '{peer.name}' — "
                "consolidation candidate",
                {"peer": peer.name, "similarity": round(score, 4)},
            )


def _duplicate_reports(context: EstateContext) -> Iterable[Finding]:
    from pbi_lineage.service.thin_reports import similar_reports

    for left, right, score in similar_reports(context.reports, threshold=0.9):
        yield Finding(
            "duplicate-report",
            SEVERITY_INFO,
            f"report:{left}",
            f"reports '{left}' and '{right}' are {score:.0%} identical",
            {"other": right, "similarity": score},
        )


def _rarely_used_fields(context: EstateContext) -> Iterable[Finding]:
    """A field used in 1 report out of many is a consolidation candidate
    even though it is technically 'used' (§5.4.7)."""
    if len(context.reports) < 5:
        return
    for (table, name), count in field_usage_frequency(context.reports).items():
        if count == 1:
            yield Finding(
                "rarely-used-field",
                SEVERITY_INFO,
                nid_column(table or "", name),
                f"{table}[{name}] is referenced by only 1 of {len(context.reports)} reports",
                {"report_count": len(context.reports)},
            )


def _stale_items(context: EstateContext) -> Iterable[Finding]:
    if context.days_since_refresh is not None and context.days_since_refresh > 90:
        yield Finding(
            "model-never-refreshed",
            SEVERITY_WARNING,
            f"model:{context.model.name}",
            f"model has not refreshed in {context.days_since_refresh} days",
            {"days": context.days_since_refresh},
        )
    for report_name in context.never_viewed_reports:
        yield Finding(
            "report-never-viewed",
            SEVERITY_INFO,
            f"report:{report_name}",
            f"report '{report_name}' has no views in the activity window — deletion candidate",
            {},
        )


def _unused_custom_visuals(context: EstateContext) -> Iterable[Finding]:
    for visual_name in context.unused_custom_visuals:
        yield Finding(
            "unused-custom-visual",
            SEVERITY_INFO,
            f"customvisual:{visual_name}",
            f"custom visual '{visual_name}' is packaged in the report but never used",
            {},
        )


def _formatting_hygiene(context: EstateContext) -> Iterable[Finding]:
    """Missing display folders, inconsistent format strings, unsorted date
    columns — the low-severity tidiness rules."""
    for table in context.model.tables:
        if len(table.measures) >= 10:
            without_folder = [m.name for m in table.measures if not m.display_folder]
            if without_folder:
                yield Finding(
                    "missing-display-folder",
                    SEVERITY_INFO,
                    nid_table(table.name),
                    f"{len(without_folder)} of {len(table.measures)} measures on {table.name} "
                    "have no display folder",
                    {"measures": without_folder[:20]},
                )
        formats = {m.format_string for m in table.measures if m.format_string}
        if len(formats) > 3:
            yield Finding(
                "inconsistent-format-strings",
                SEVERITY_INFO,
                nid_table(table.name),
                f"{table.name} uses {len(formats)} distinct measure format strings",
                {"formats": sorted(formats)[:20]},
            )
        for column in table.columns:
            if (column.data_type or "").lower() == "datetime" and column.sort_by_column is None:
                if any(
                    other.name != column.name and (other.data_type or "").lower() == "string"
                    for other in table.columns
                ):
                    yield Finding(
                        "unsorted-date-column",
                        SEVERITY_INFO,
                        nid_column(table.name, column.name),
                        f"{table.name}[{column.name}] is a date column with no sort-by column",
                        {},
                    )


DEFAULT_RULES: list[Rule] = [
    Rule("implicit-measure", SEVERITY_WARNING, "Implicit measures in use", _implicit_measures),
    Rule("broken-visual-reference", SEVERITY_ERROR, "Visuals referencing missing fields", _broken_visuals),
    # `_broken_visuals` also emits `auto-date-reference` and
    # `unresolved-reference`; they are declared so the UI can name them.
    Rule("unresolved-reference", SEVERITY_WARNING, "References that name no table", lambda _: ()),
    Rule("auto-date-reference", SEVERITY_INFO, "Auto date/time table references", lambda _: ()),
    Rule("broken-dax", SEVERITY_ERROR, "DAX referencing deleted objects", _broken_dax),
    Rule("auto-date-table", SEVERITY_WARNING, "Auto date/time tables enabled", _auto_date_tables),
    Rule(
        "relationship-hygiene",
        SEVERITY_WARNING,
        "Bi-directional and many-to-many relationships",
        _bidirectional_relationships,
    ),
    Rule(
        "high-cardinality-unused-column",
        SEVERITY_WARNING,
        "High-cardinality columns with no usage",
        _high_cardinality_unused,
    ),
    Rule("duplicate-model", SEVERITY_WARNING, "Duplicate / near-duplicate models", _duplicate_models),
    Rule("duplicate-report", SEVERITY_INFO, "Duplicate reports", _duplicate_reports),
    Rule("rarely-used-field", SEVERITY_INFO, "Fields used by a single report", _rarely_used_fields),
    Rule("stale-items", SEVERITY_INFO, "Models never refreshed / reports never viewed", _stale_items),
    Rule("unused-custom-visual", SEVERITY_INFO, "Unused custom visuals", _unused_custom_visuals),
    Rule("formatting-hygiene", SEVERITY_INFO, "Display folders, formats, date sorting", _formatting_hygiene),
]


def run_rules(context: EstateContext, rules: list[Rule] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules or DEFAULT_RULES:
        if not rule.enabled:
            continue
        try:
            findings.extend(rule.check(context))
        except Exception as exc:  # noqa: BLE001 — one bad rule must not kill the run
            findings.append(
                Finding(
                    rule.id,
                    SEVERITY_WARNING,
                    f"rule:{rule.id}",
                    f"rule '{rule.id}' failed to evaluate: {exc}",
                    {},
                )
            )
    order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    return sorted(findings, key=lambda f: (order.get(f.severity, 3), f.rule_id, f.object_id))
