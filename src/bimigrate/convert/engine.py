"""Conversion engine: expression in -> ConversionDecision out.

Pipeline per expression (Section 6):
1. AST parse with the dialect parser; emit DAX via the KB-driven emitter.
2. On parse failure, fall back to KB regex rules for AUTO-tier simple
   patterns (single-function rewrites only).
3. Otherwise MANUAL: emit fallback documentation, never partial DAX.

Every decision carries confidence, tier, cited rule ids and annotations.
A parse failure or unknown function can never raise out of convert_*.
"""

from __future__ import annotations

import re

from bimigrate.convert.dax_emitter import DaxEmitter, FieldResolver
from bimigrate.convert.expr_parser import ExpressionParseError, parse_expression
from bimigrate.kb.knowledge_base import KnowledgeBase
from bimigrate.models.core import ConfidenceTier, ConversionDecision, tier_for_confidence
from bimigrate.models.inventory import ArtifactInventory, Calculation

_DIALECT_BY_TOOL = {
    "tableau": "tableau",
    "spotfire": "spotfire",
    "qlikview": "qlik",
    "qliksense": "qlik",
}


class ConversionEngine:
    def __init__(
        self, kb: KnowledgeBase, field_resolver: FieldResolver | None = None, table_hint: str = "Data"
    ):
        self.kb = kb
        self.field_resolver = field_resolver
        self.table_hint = table_hint

    def convert_expression(
        self,
        source_tool: str,
        expression: str,
        artifact: str = "",
        feature_mapping: str | None = None,
    ) -> ConversionDecision:
        dialect = _DIALECT_BY_TOOL.get(source_tool, "tableau")
        try:
            ast = parse_expression(expression, dialect)
        except ExpressionParseError as exc:
            return self._regex_fallback(source_tool, expression, artifact, str(exc), feature_mapping)

        emitter = DaxEmitter(self.kb, source_tool, self.field_resolver, self.table_hint)
        result = emitter.emit(ast)
        tier = tier_for_confidence(result.confidence)
        target = result.dax if tier != ConfidenceTier.MANUAL else None
        decision = ConversionDecision(
            source_tool=source_tool,
            source_artifact=artifact,
            source_expression=expression,
            target_expression=target,
            tier=tier,
            confidence=round(result.confidence, 3),
            rule_id=self._binding_rule(result.rule_ids),
            feature_mapping=feature_mapping,
            annotations=sorted(set(result.annotations)),
        )
        if tier == ConfidenceTier.MANUAL:
            decision.fallback_strategy = (
                "Below auto-conversion threshold. Draft DAX (do not deploy): " f"{result.dax}"
                if result.dax
                else "No draft available."
            )
        return decision

    def _binding_rule(self, rule_ids: list[str]) -> str | None:
        """Cite the binding-constraint rule: the lowest-confidence rule among
        all rules touched while emitting (that one set the tier)."""
        if not rule_ids:
            return None
        known = [(self.kb.get_rule(rid), rid) for rid in dict.fromkeys(rule_ids)]
        scored = [(rule.confidence, rid) for rule, rid in known if rule is not None]
        if not scored:
            return rule_ids[0]
        return min(scored)[1]

    def _regex_fallback(
        self,
        source_tool: str,
        expression: str,
        artifact: str,
        parse_error: str,
        feature_mapping: str | None,
    ) -> ConversionDecision:
        """AST failed; only a whole-expression single-function AUTO rule may apply."""
        for rule in self.kb.rules_for_tool(source_tool):
            if rule.tier != "AUTO" or rule.source_pattern.startswith("ast:"):
                continue
            try:
                m = re.fullmatch(
                    rule.source_pattern.rstrip("(").rstrip("\\s*") + r"\((?P<arg>[^()]*)\)\s*",
                    expression.strip(),
                )
            except re.error:
                continue
            if m:
                target = rule.dax_template.replace("{0}", m.group("arg").strip())
                return ConversionDecision(
                    source_tool=source_tool,
                    source_artifact=artifact,
                    source_expression=expression,
                    target_expression=target,
                    tier=ConfidenceTier.ASSISTED,  # downgraded: regex-only path
                    confidence=min(rule.confidence, 0.85),
                    rule_id=rule.rule_id,
                    feature_mapping=feature_mapping,
                    annotations=[f"AST parse failed ({parse_error}); regex rule applied — review"],
                )
        return ConversionDecision(
            source_tool=source_tool,
            source_artifact=artifact,
            source_expression=expression,
            target_expression=None,
            tier=ConfidenceTier.MANUAL,
            confidence=0.0,
            feature_mapping=feature_mapping,
            fallback_strategy="Expression could not be parsed; manual conversion required.",
            annotations=[f"parse error: {parse_error}"],
        )

    # -- batch over an inventory ------------------------------------------------

    def convert_inventory(self, inv: ArtifactInventory) -> list[ConversionDecision]:
        decisions = []
        for calc in inv.calculations:
            if calc.kind == "variable" and not _looks_like_expression(calc):
                continue
            decisions.append(
                self.convert_expression(
                    inv.source_tool.value,
                    calc.expression,
                    artifact=f"{inv.artifact_name}/{calc.name}",
                    feature_mapping=_FEATURE_BY_KIND.get(calc.kind),
                )
            )
        return decisions


_FEATURE_BY_KIND = {
    "calculated_field": "Calculated field (row-level)",
    "table_calc": "Table calculation",
    "lod": "LOD FIXED",
    "set_analysis": "Set analysis",
    "measure": "Master measures",
    "variable": "Variables (expression)",
}


def _looks_like_expression(calc: Calculation) -> bool:
    text = calc.expression.strip()
    return text.startswith("=") or "(" in text
