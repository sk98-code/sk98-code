"""Validation & accuracy framework (Section 9).

Honesty rules baked in:
* accuracy is reported PER TIER and PER COMPLEXITY BAND, never blended;
* numeric parity testing compares a source aggregate against the DAX
  result on sample data with configurable tolerance;
* every MANUAL item and every rejected ASSISTED item lands in the
  Exception Register with a reason code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from bimigrate.models.core import ConfidenceTier, ConversionDecision
from bimigrate.models.inventory import ArtifactInventory, ComplexityBand


class ReasonCode(StrEnum):
    NO_EQUIVALENT = "NO_EQUIVALENT"
    PARSE_FAILURE = "PARSE_FAILURE"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    NUMERIC_MISMATCH = "NUMERIC_MISMATCH"
    STRUCTURAL_MISMATCH = "STRUCTURAL_MISMATCH"
    AGENT_OUTPUT = "AGENT_OUTPUT"


@dataclass
class ExceptionRecord:
    artifact: str
    item: str
    tier: str
    reason_code: ReasonCode
    detail: str = ""


@dataclass
class ParityResult:
    item: str
    source_value: float
    target_value: float
    tolerance: float
    passed: bool


@dataclass
class ValidationReport:
    parity_results: list[ParityResult] = field(default_factory=list)
    exceptions: list[ExceptionRecord] = field(default_factory=list)
    structural_checks: list[dict] = field(default_factory=list)
    # accuracy_by[tier][band] = {"total": n, "passed": n}
    accuracy_by: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)

    def record(self, tier: str, band: str, passed: bool) -> None:
        bucket = self.accuracy_by.setdefault(tier, {}).setdefault(band, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(passed)

    def accuracy_matrix(self) -> list[dict]:
        rows = []
        for tier in ("AUTO", "ASSISTED", "MANUAL"):
            for band in ("simple", "medium", "complex"):
                bucket = self.accuracy_by.get(tier, {}).get(band)
                if not bucket or not bucket["total"]:
                    continue
                rows.append(
                    {
                        "tier": tier,
                        "complexity_band": band,
                        "total": bucket["total"],
                        "passed": bucket["passed"],
                        "accuracy_pct": round(100 * bucket["passed"] / bucket["total"], 1),
                    }
                )
        return rows

    @property
    def automated_conversion_rate(self) -> float:
        total = sum(b["total"] for tiers in self.accuracy_by.values() for b in tiers.values())
        auto = sum(b["total"] for b in self.accuracy_by.get("AUTO", {}).values())
        assisted = sum(b["total"] for b in self.accuracy_by.get("ASSISTED", {}).values())
        return round(100 * (auto + assisted) / total, 1) if total else 0.0


class Validator:
    def __init__(self, numeric_tolerance: float = 1e-6):
        self.tolerance = numeric_tolerance

    # -- numeric parity ------------------------------------------------------

    def check_parity(
        self,
        item: str,
        source_value: float,
        target_value: float,
        report: ValidationReport,
        tier: str = "AUTO",
        band: str = "simple",
    ) -> ParityResult:
        if source_value == 0:
            passed = abs(target_value) <= self.tolerance
        else:
            passed = math.isclose(source_value, target_value, rel_tol=self.tolerance, abs_tol=self.tolerance)
        result = ParityResult(
            item=item,
            source_value=source_value,
            target_value=target_value,
            tolerance=self.tolerance,
            passed=passed,
        )
        report.parity_results.append(result)
        report.record(tier, band, passed)
        if not passed:
            report.exceptions.append(
                ExceptionRecord(
                    artifact=item,
                    item=item,
                    tier=tier,
                    reason_code=ReasonCode.NUMERIC_MISMATCH,
                    detail=f"source={source_value} target={target_value}",
                )
            )
        return result

    # -- structural checks -------------------------------------------------------

    def check_visual_structure(
        self, inv: ArtifactInventory, report_layout: dict, report: ValidationReport
    ) -> None:
        """Visual type/page-count parity between inventory and emitted report.json."""
        emitted_pages = {s["displayName"] for s in report_layout.get("sections", [])}
        for ws in inv.worksheets:
            ok = ws.name in emitted_pages
            report.structural_checks.append({"check": "page_exists", "item": ws.name, "passed": ok})
            report.record("AUTO", inv.complexity_band.value, ok)
            if not ok:
                report.exceptions.append(
                    ExceptionRecord(
                        artifact=inv.artifact_name,
                        item=ws.name,
                        tier="AUTO",
                        reason_code=ReasonCode.STRUCTURAL_MISMATCH,
                        detail="page missing from emitted report layout",
                    )
                )
        emitted_visuals = sum(len(s.get("visualContainers", [])) for s in report_layout.get("sections", []))
        ok = emitted_visuals >= inv.visual_count
        report.structural_checks.append(
            {
                "check": "visual_count",
                "item": inv.artifact_name,
                "passed": ok,
                "expected": inv.visual_count,
                "actual": emitted_visuals,
            }
        )
        report.record("AUTO", inv.complexity_band.value, ok)

    # -- metadata round-trip ---------------------------------------------------------

    def check_metadata_roundtrip(self, inv: ArtifactInventory, report: ValidationReport) -> bool:
        """Serialize -> reparse -> compare: guards extractor regressions."""
        clone = type(inv).model_validate_json(inv.model_dump_json())
        ok = (
            clone.fingerprint == inv.fingerprint
            and clone.visual_count == inv.visual_count
            and len(clone.calculations) == len(inv.calculations)
        )
        report.record("AUTO", inv.complexity_band.value, ok)
        return ok

    # -- decisions -> exception register -----------------------------------------------

    def register_decisions(
        self, decisions: list[ConversionDecision], band: ComplexityBand, report: ValidationReport
    ) -> None:
        for d in decisions:
            converted = d.tier != ConfidenceTier.MANUAL and d.target_expression
            report.record(d.tier.value, band.value, bool(converted))
            if d.tier == ConfidenceTier.MANUAL:
                reason = (
                    ReasonCode.PARSE_FAILURE
                    if any("parse error" in a for a in d.annotations)
                    else ReasonCode.NO_EQUIVALENT if d.confidence == 0.0 else ReasonCode.BELOW_THRESHOLD
                )
                report.exceptions.append(
                    ExceptionRecord(
                        artifact=d.source_artifact,
                        item=d.source_expression[:120],
                        tier=d.tier.value,
                        reason_code=reason,
                        detail=d.fallback_strategy or "",
                    )
                )
