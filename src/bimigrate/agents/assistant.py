"""AI migration assistant (Section 8).

LLM-pluggable: any callable(prompt) -> str works as a client, so the
Anthropic API, a local model, or a test stub plug in identically. The
Anthropic client is an optional dependency (`pip install bimigrate[agents]`).

Guardrails (enforced here, not by convention):
* agents NEVER write AUTO-tier output — anything agent-produced is
  capped at ASSISTED and marked produced_by_agent=True;
* every agent rewrite logs a diff entry;
* explanations must cite FeatureMapping / ConversionRule records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bimigrate.kb.knowledge_base import KnowledgeBase
from bimigrate.mapping.repository import MappingRepository
from bimigrate.models.core import ConfidenceTier, ConversionDecision

LlmClient = Callable[[str], str]


class AgentGuardrailError(Exception):
    pass


@dataclass
class AgentDiffEntry:
    item: str
    before: str | None
    after: str
    rationale: str


def anthropic_client(model: str = "claude-sonnet-4-6", api_key: str | None = None) -> LlmClient:
    """Build an Anthropic-backed client (requires the optional dependency)."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ImportError("install the agents extra: pip install 'bimigrate[agents]'") from exc
    sdk = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def call(prompt: str) -> str:
        response = sdk.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")

    return call


class MigrationAssistant:
    def __init__(self, llm: LlmClient, kb: KnowledgeBase, mappings: MappingRepository):
        self.llm = llm
        self.kb = kb
        self.mappings = mappings
        self.diff_log: list[AgentDiffEntry] = []

    # -- explain ------------------------------------------------------------

    def explain_decision(self, decision: ConversionDecision) -> str:
        citations = []
        if decision.rule_id and (rule := self.kb.get_rule(decision.rule_id)):
            citations.append(
                f"ConversionRule {rule.rule_id} (confidence {rule.confidence}, "
                f"tier {rule.tier}): {rule.source_example} -> {rule.dax_example}"
            )
        if decision.feature_mapping and (
            m := self.mappings.get(decision.source_tool, decision.feature_mapping)
        ):
            citations.append(
                f"FeatureMapping '{m.feature_name}' -> "
                f"{m.powerbi_equivalent or '(no equivalent)'}; fallback: {m.fallback_strategy}"
            )
        prompt = (
            "Explain this BI migration conversion decision to a reviewer. Cite the "
            "repository records verbatim; do not invent mappings.\n\n"
            f"Source ({decision.source_tool}): {decision.source_expression}\n"
            f"Target: {decision.target_expression or '(not converted)'}\n"
            f"Tier: {decision.tier.value} (confidence {decision.confidence})\n"
            f"Annotations: {decision.annotations}\n"
            "Repository citations:\n" + "\n".join(f"- {c}" for c in citations)
        )
        return self.llm(prompt)

    # -- rewrite (guarded) ------------------------------------------------------

    def rewrite_failed_conversion(self, decision: ConversionDecision) -> ConversionDecision:
        """Ask the LLM to draft DAX for an item the AST pipeline could not
        convert. Output is ALWAYS ASSISTED-tier max, never AUTO."""
        prompt = (
            f"Convert this {decision.source_tool} expression to a single DAX "
            "expression. Reply with ONLY the DAX, no prose, no fences.\n\n"
            f"{decision.source_expression}\n\n"
            f"Known issues: {'; '.join(decision.annotations) or 'none'}"
        )
        draft = self.llm(prompt).strip().strip("`")
        if not draft:
            raise AgentGuardrailError("agent returned empty rewrite")

        rewritten = decision.model_copy(deep=True)
        rewritten.target_expression = draft
        rewritten.produced_by_agent = True
        # guardrail: agent output is capped at ASSISTED regardless of optimism
        rewritten.confidence = min(max(decision.confidence, 0.60), 0.89)
        rewritten.tier = ConfidenceTier.ASSISTED
        rewritten.annotations = sorted(
            set(decision.annotations + ["agent-generated conversion: human review REQUIRED"])
        )
        self.diff_log.append(
            AgentDiffEntry(
                item=decision.source_artifact,
                before=decision.target_expression,
                after=draft,
                rationale="AST conversion failed; agent rewrite",
            )
        )
        return rewritten

    def assert_not_auto(self, decision: ConversionDecision) -> None:
        if decision.produced_by_agent and decision.tier == ConfidenceTier.AUTO:
            raise AgentGuardrailError("agent-produced conversions must never carry the AUTO tier")

    # -- advisory functions --------------------------------------------------------

    def suggest_visual_equivalent(self, source_tool: str, visual_type: str) -> str:
        unsupported = [
            m for m in self.mappings.unsupported(source_tool) if m.source_layer in ("visual", "visualization")
        ]
        context = "\n".join(f"- {m.feature_name}: fallback = {m.fallback_strategy}" for m in unsupported)
        prompt = (
            f"A {source_tool} report uses a '{visual_type}' visual with no native "
            "Power BI mapping. Recommend the closest Power BI approach. Known "
            f"fallbacks from the mapping repository:\n{context}\n"
            "Give one concrete recommendation with a short build note."
        )
        return self.llm(prompt)

    def optimize_dax(self, dax: str) -> tuple[str, str]:
        """Returns (optimized_dax, rationale). Logged, never auto-applied."""
        prompt = (
            "Optimize this DAX measure: prefer VARs over repeated expressions, avoid "
            "nested iterators, keep filter context behavior IDENTICAL. Reply as:\n"
            "DAX:\n<code>\nRATIONALE:\n<text>\n\n" + dax
        )
        reply = self.llm(prompt)
        optimized, _, rationale = reply.partition("RATIONALE:")
        optimized = optimized.replace("DAX:", "").strip().strip("`")
        self.diff_log.append(
            AgentDiffEntry(
                item="dax-optimize", before=dax, after=optimized or dax, rationale=rationale.strip()
            )
        )
        return optimized or dax, rationale.strip()

    def generate_runbook(
        self, artifact_name: str, source_tool: str, decisions: list[ConversionDecision]
    ) -> str:
        manual = [d for d in decisions if d.tier == ConfidenceTier.MANUAL]
        assisted = [d for d in decisions if d.tier == ConfidenceTier.ASSISTED]
        prompt = (
            f"Write a per-report migration runbook (markdown) for '{artifact_name}' "
            f"({source_tool} -> Power BI). {len(decisions)} expressions total, "
            f"{len(assisted)} need review, {len(manual)} are manual.\n"
            "Manual items with fallbacks:\n"
            + "\n".join(f"- {d.source_expression[:100]} :: {d.fallback_strategy}" for d in manual[:30])
        )
        return self.llm(prompt)
