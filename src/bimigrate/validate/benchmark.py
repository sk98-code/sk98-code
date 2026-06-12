"""Accuracy benchmark harness (Section 9 / Phase 4).

Runs the conversion engine over a curated corpus of real-world expression
shapes with expected DAX fragments and expected tiers, then scores
accuracy per tier x complexity band. This is the regression gate for the
rule set: any KB change that flips a tier or breaks an expected output
shows up here before it reaches a client estate.

A pass requires BOTH:
* tier match — converting a MANUAL-expected item is scored as a failure
  (over-conversion is a defect, not a win), and
* every expected fragment present in the emitted DAX (for convertible
  items).

The shipped corpus is a seed; engagements append their pilot estates'
golden cases via extra JSON files (same record shape).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from bimigrate.convert.engine import ConversionEngine
from bimigrate.kb.knowledge_base import KnowledgeBase
from bimigrate.models.inventory import ComplexityBand
from bimigrate.validate.framework import ValidationReport

# (tool, expression, expected_fragments, expected_tier, band)
SEED_CORPUS: list[tuple] = [
    # -- tableau / simple --
    ("tableau", "SUM([Sales])", ["SUM("], "AUTO", "simple"),
    ("tableau", "COUNTD([Customer])", ["DISTINCTCOUNT("], "AUTO", "simple"),
    ("tableau", "ZN(SUM([Profit]))", ["COALESCE(", ", 0)"], "AUTO", "simple"),
    ("tableau", "IFNULL([Region], 'Unknown')", ["COALESCE("], "AUTO", "simple"),
    ("tableau", "LEFT([Name], 3)", ["LEFT("], "AUTO", "simple"),
    ("tableau", "UPPER(TRIM([Code]))", ["UPPER(", "TRIM("], "AUTO", "simple"),
    ("tableau", "MAKEDATE(2024, 1, 15)", ["DATE(2024, 1, 15)"], "AUTO", "simple"),
    ("tableau", "IIF([Sales] > 0, 1, 0)", ["IF("], "AUTO", "simple"),
    # -- tableau / medium --
    ("tableau", "DATEDIFF('month', [Start], [End])", ["DATEDIFF(", "MONTH"], "AUTO", "medium"),
    (
        "tableau",
        "IF [S] > 100 THEN 'H' ELSEIF [S] > 50 THEN 'M' ELSE 'L' END",
        ["SWITCH(TRUE()"],
        "AUTO",
        "medium",
    ),
    ("tableau", "CASE [Region] WHEN 'EU' THEN 1 WHEN 'US' THEN 2 ELSE 0 END", ["SWITCH("], "AUTO", "medium"),
    ("tableau", "{ FIXED [Customer] : SUM([Sales]) }", ["CALCULATE(", "ALLEXCEPT("], "ASSISTED", "medium"),
    ("tableau", "{ EXCLUDE [Month] : SUM([Sales]) }", ["REMOVEFILTERS("], "ASSISTED", "medium"),
    ("tableau", "RUNNING_SUM(SUM([Sales]))", ["WINDOW("], "ASSISTED", "medium"),
    ("tableau", "RANK(SUM([Sales]))", ["RANKX("], "ASSISTED", "medium"),
    ("tableau", "TOTAL(SUM([Sales]))", ["ALLSELECTED()"], "ASSISTED", "medium"),
    # -- tableau / complex --
    ("tableau", "{ FIXED [A] : SUM({ INCLUDE [B] : MAX([X]) }) }", ["CALCULATE("], "ASSISTED", "complex"),
    ("tableau", "WINDOW_AVG(SUM([Sales]), -2, 0)", ["AVERAGEX(", "WINDOW("], "ASSISTED", "complex"),
    ("tableau", "REGEXP_EXTRACT([Code], '(\\d+)')", [], "MANUAL", "complex"),
    ("tableau", "SCRIPT_REAL('lm(y~x)', SUM([Y]))", [], "MANUAL", "complex"),
    ("tableau", "PREVIOUS_VALUE(0) + SUM([Delta])", [], "MANUAL", "complex"),
    # -- qlik / simple --
    ("qlikview", "Sum(Sales)", ["SUM("], "AUTO", "simple"),
    ("qlikview", "Count(DISTINCT CustomerID)", ["DISTINCTCOUNT("], "AUTO", "simple"),
    ("qlikview", "Alt(Discount, 0)", ["COALESCE("], "AUTO", "simple"),
    ("qlikview", "Year(Today())", ["YEAR(TODAY())"], "AUTO", "simple"),
    ("qlikview", "Upper(Trim(Code))", ["UPPER(", "TRIM("], "AUTO", "simple"),
    ("qliksense", "NetWorkDays(StartDate, EndDate)", ["NETWORKDAYS("], "AUTO", "simple"),
    # -- qlik / medium --
    ("qlikview", "Sum({<Year={2024}>} Sales)", ["CALCULATE(", "= 2024"], "ASSISTED", "medium"),
    ("qlikview", "Sum({<Region-={'EU'}>} Sales)", ["<>"], "ASSISTED", "medium"),
    ("qlikview", "Sum({<Year=>} Sales)", ["REMOVEFILTERS("], "ASSISTED", "medium"),
    ("qlikview", "Sum({1} Sales)", ["REMOVEFILTERS()"], "ASSISTED", "medium"),
    ("qlikview", "Sum(TOTAL Sales)", ["ALLSELECTED()"], "ASSISTED", "medium"),
    ("qliksense", "If(Sum(Sales) > 1000, 'High', 'Low')", ["IF("], "AUTO", "medium"),
    ("qlikview", "Rank(Sum(Sales))", ["RANKX("], "ASSISTED", "medium"),
    # -- qlik / complex --
    ("qlikview", "Avg(Aggr(Sum(Sales), Customer))", ["SUMMARIZE("], "ASSISTED", "complex"),
    ("qlikview", "Sum({<Customer=P({<Year={2024}>} Customer)>} Sales)", [], "ASSISTED", "complex"),
    ("qlikview", "Sum({State1<Year={2024}>} Sales)", [], "MANUAL", "complex"),
    ("qliksense", "Sum({AltState<Region={'EU'}>} Sales)", [], "MANUAL", "complex"),
    # -- spotfire / simple --
    ("spotfire", "Sum([Sales])", ["SUM("], "AUTO", "simple"),
    ("spotfire", "UniqueCount([Customer])", ["DISTINCTCOUNT("], "AUTO", "simple"),
    ("spotfire", "SN([Amount], 0)", ["COALESCE("], "AUTO", "simple"),
    ("spotfire", "Round([Price], 2)", ["ROUND("], "AUTO", "simple"),
    # -- spotfire / medium --
    ("spotfire", "CASE WHEN [S] > 100 THEN 'High' ELSE 'Low' END", ["SWITCH(TRUE()"], "AUTO", "medium"),
    ("spotfire", "Sum([Sales]) OVER (All([Axis.X]))", ["ALLSELECTED("], "ASSISTED", "medium"),
    (
        "spotfire",
        "Sum([Sales]) OVER (AllPrevious([OrderDate]))",
        ["FILTER(ALLSELECTED("],
        "ASSISTED",
        "medium",
    ),
    ("spotfire", "Percentile([Amount], 90)", ["PERCENTILE.INC("], "AUTO", "medium"),
    # -- spotfire / complex --
    ("spotfire", "Sum([Sales]) OVER (Intersect([Hier], AllPrevious([Axis.X])))", [], "ASSISTED", "complex"),
    (
        "spotfire",
        "Sum([Sales]) THEN [Value] / Max([Value]) OVER (All([Axis.X]))",
        ["VAR _stage"],
        "ASSISTED",
        "complex",
    ),
]


@dataclass
class BenchmarkCase:
    tool: str
    expression: str
    expected_fragments: list[str]
    expected_tier: str
    band: str


@dataclass
class BenchmarkResult:
    report: ValidationReport
    failures: list[dict] = field(default_factory=list)
    total: int = 0
    passed: int = 0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "accuracy_matrix": self.report.accuracy_matrix(),
            "automated_conversion_rate": self.report.automated_conversion_rate,
            "failures": self.failures,
        }


def load_corpus(extra_files: list[Path] | None = None) -> list[BenchmarkCase]:
    cases = [BenchmarkCase(*row) for row in SEED_CORPUS]
    for path in extra_files or []:
        for row in json.loads(path.read_text()):
            cases.append(
                BenchmarkCase(
                    tool=row["tool"],
                    expression=row["expression"],
                    expected_fragments=row.get("expected_fragments", []),
                    expected_tier=row["expected_tier"],
                    band=row.get("band", "medium"),
                )
            )
    return cases


def run_benchmark(kb: KnowledgeBase | None = None, extra_files: list[Path] | None = None) -> BenchmarkResult:
    own_kb = kb is None
    if own_kb:
        kb = KnowledgeBase()
        kb.init_from_seeds()
    engine = ConversionEngine(kb)
    result = BenchmarkResult(report=ValidationReport())

    for case in load_corpus(extra_files):
        decision = engine.convert_expression(case.tool, case.expression, artifact="benchmark")
        tier_ok = decision.tier.value == case.expected_tier
        fragments_ok = True
        if case.expected_tier != "MANUAL" and case.expected_fragments:
            target = decision.target_expression or ""
            fragments_ok = all(f in target for f in case.expected_fragments)
        passed = tier_ok and fragments_ok
        result.total += 1
        result.passed += int(passed)
        result.report.record(case.expected_tier, case.band, passed)
        if not passed:
            result.failures.append(
                {
                    "tool": case.tool,
                    "expression": case.expression,
                    "expected_tier": case.expected_tier,
                    "actual_tier": decision.tier.value,
                    "expected_fragments": case.expected_fragments,
                    "actual": decision.target_expression,
                    "annotations": decision.annotations,
                }
            )
    if own_kb:
        kb.close()
    return result


_ = ComplexityBand  # bands in the corpus align with ComplexityBand values
