"""Core schemas shared by every subsystem.

The confidence-tier policy lives here so that every part of the pipeline
(mapping repository, conversion engine, agents, validation) makes the same
AUTO / ASSISTED / MANUAL decision from the same numbers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SourceTool(StrEnum):
    TABLEAU = "tableau"
    SPOTFIRE = "spotfire"
    QLIKVIEW = "qlikview"
    QLIKSENSE = "qliksense"


class ConfidenceTier(StrEnum):
    AUTO = "AUTO"  # >= 0.90: convert automatically, log for spot-check
    ASSISTED = "ASSISTED"  # 0.60 - 0.89: convert + flag for human review
    MANUAL = "MANUAL"  # < 0.60: do NOT auto-convert; emit fallback strategy


AUTO_THRESHOLD = 0.90
ASSISTED_THRESHOLD = 0.60


def tier_for_confidence(confidence: float) -> ConfidenceTier:
    """Single source of truth for the tier policy (Section 5 of the spec)."""
    if confidence >= AUTO_THRESHOLD:
        return ConfidenceTier.AUTO
    if confidence >= ASSISTED_THRESHOLD:
        return ConfidenceTier.ASSISTED
    return ConfidenceTier.MANUAL


class MigrationComplexity(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    VERY_COMPLEX = "very_complex"


class AutomationPossibility(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    MANUAL = "manual"


class FeatureMapping(BaseModel):
    """One row of the Feature Mapping Repository (Section 5)."""

    feature_name: str
    source_tool: Literal["tableau", "spotfire", "qlikview", "qliksense"]
    source_layer: str  # e.g. "calculation", "visual", "security"
    powerbi_equivalent: str | None  # None = no native equivalent
    migration_complexity: Literal["simple", "medium", "complex", "very_complex"]
    automation_possibility: Literal["full", "partial", "manual"]
    fallback_strategy: str  # MANDATORY when equivalent is None/partial
    confidence_score: float = Field(ge=0.0, le=1.0)
    notes: str = ""
    example_source: str | None = None
    example_target: str | None = None

    @field_validator("fallback_strategy")
    @classmethod
    def _fallback_required(cls, v: str, info) -> str:
        equivalent = info.data.get("powerbi_equivalent")
        automation = info.data.get("automation_possibility")
        if (equivalent is None or automation in ("partial", "manual")) and not v.strip():
            raise ValueError(
                "fallback_strategy is mandatory when powerbi_equivalent is None "
                "or automation_possibility is partial/manual"
            )
        return v

    @property
    def tier(self) -> ConfidenceTier:
        return tier_for_confidence(self.confidence_score)


class ConversionRule(BaseModel):
    """One data-driven conversion rule (Section 6). Rules are stored in the
    knowledge base / SQLite, never hardcoded in converter code."""

    rule_id: str
    source_tool: str
    source_pattern: str  # regex or AST pattern (prefix "ast:" for AST patterns)
    source_example: str
    dax_template: str  # parameterized output template, {0}, {1}... placeholders
    dax_example: str
    confidence: float = Field(ge=0.0, le=1.0)
    preconditions: list[str] = Field(default_factory=list)
    known_failure_modes: list[str] = Field(default_factory=list)
    tier: Literal["AUTO", "ASSISTED", "MANUAL"]

    @field_validator("tier")
    @classmethod
    def _tier_matches_confidence(cls, v: str, info) -> str:
        confidence = info.data.get("confidence")
        if confidence is not None and tier_for_confidence(confidence).value != v:
            raise ValueError(
                f"tier {v!r} inconsistent with confidence {confidence} "
                f"(policy says {tier_for_confidence(confidence).value})"
            )
        return v


class ConversionDecision(BaseModel):
    """Traceability record: every conversion cites the mapping/rule used."""

    source_tool: str
    source_artifact: str  # file or object the input came from
    source_expression: str
    target_expression: str | None
    tier: ConfidenceTier
    confidence: float
    rule_id: str | None = None  # ConversionRule applied, if any
    feature_mapping: str | None = None  # FeatureMapping.feature_name cited
    fallback_strategy: str | None = None  # populated for MANUAL outcomes
    annotations: list[str] = Field(default_factory=list)
    produced_by_agent: bool = False  # agent output is never AUTO (Section 8)
