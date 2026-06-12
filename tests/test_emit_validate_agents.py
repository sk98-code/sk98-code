import json
from pathlib import Path

import pytest

from bimigrate.agents import AgentGuardrailError, MigrationAssistant
from bimigrate.convert import ConversionEngine
from bimigrate.emit.model_builder import build_model
from bimigrate.emit.pbip import PbipEmitter
from bimigrate.emit.rls import build_rls
from bimigrate.emit.tmdl import TmdlColumn, TmdlMeasure, TmdlModel, TmdlRole, TmdlTable
from bimigrate.kb import KnowledgeBase
from bimigrate.mapping import MappingRepository
from bimigrate.models.core import ConfidenceTier, ConversionDecision
from bimigrate.parsers import QlikViewParser, TableauParser
from bimigrate.validate import ValidationReport, Validator


def test_tmdl_render_and_write(tmp_path: Path):
    model = TmdlModel(
        name="SalesModel",
        tables=[
            TmdlTable(
                name="Sales",
                columns=[TmdlColumn(name="Region"), TmdlColumn(name="Order Date", data_type="dateTime")],
                measures=[
                    TmdlMeasure(
                        name="Total Sales", expression="SUM(Sales[Amount])", display_folder="Migrated"
                    )
                ],
                partition_m="let\n    Source = null\nin\n    Source",
            )
        ],
        roles=[TmdlRole(name="EU_Only", table_filters={"Sales": '[Region] = "EU"'})],
    )
    files = model.write(tmp_path / "definition")
    table_tmdl = (tmp_path / "definition/tables/Sales.tmdl").read_text()
    assert "measure 'Total Sales' = SUM(Sales[Amount])" in table_tmdl
    assert "column 'Order Date'" in table_tmdl
    assert "dataType: dateTime" in table_tmdl
    assert (tmp_path / "definition/roles/EU_Only.tmdl").exists()
    assert any(p.name == "model.tmdl" for p in files)


def test_pbip_emit_opens_as_valid_json(estate_dir: Path, tmp_path: Path):
    inv = TableauParser().parse(estate_dir / "sales.twb")
    kb = KnowledgeBase()
    kb.init_from_seeds()
    decisions = ConversionEngine(kb).convert_inventory(inv)
    model = build_model(inv, decisions)
    project = PbipEmitter(tmp_path / "out").emit(inv, model)

    pbip = json.loads((project / "sales.pbip").read_text())
    assert pbip["artifacts"][0]["report"]["path"] == "sales.Report"
    report = json.loads((project / "sales.Report/report.json").read_text())
    assert {s["displayName"] for s in report["sections"]} == {"Sales by Region", "Trend"}
    pbir = json.loads((project / "sales.Report/definition.pbir").read_text())
    assert pbir["datasetReference"]["byPath"]["path"] == "../sales.SemanticModel"
    assert (project / "sales.SemanticModel/definition/model.tmdl").exists()
    theme = json.loads(
        (project / "sales.Report/StaticResources/RegisteredResources/migrated_theme.json").read_text()
    )
    assert theme["dataColors"]
    kb.close()


def test_rls_from_section_access(estate_dir: Path):
    inv = QlikViewParser().parse(estate_dir / "reload.qvs")
    proposal = build_rls(inv)
    assert proposal.needs_user_mapping_table
    assert any(r.name == "SectionAccess_Users" for r in proposal.roles)
    assert any("OMIT" in a for a in proposal.annotations)


def test_validator_accuracy_per_tier_and_band(estate_dir: Path):
    inv = TableauParser().parse(estate_dir / "sales.twb")
    from bimigrate.engine.complexity import score_inventory

    score_inventory(inv)
    kb = KnowledgeBase()
    kb.init_from_seeds()
    decisions = ConversionEngine(kb).convert_inventory(inv)
    report = ValidationReport()
    validator = Validator()
    validator.register_decisions(decisions, inv.complexity_band, report)
    matrix = report.accuracy_matrix()
    assert matrix  # never empty for a workbook with calcs
    assert all({"tier", "complexity_band", "accuracy_pct"} <= set(r) for r in matrix)
    assert validator.check_metadata_roundtrip(inv, report)
    kb.close()


def test_validator_numeric_parity():
    report = ValidationReport()
    v = Validator(numeric_tolerance=0.01)
    assert v.check_parity("m1", 100.0, 100.001, report).passed
    assert not v.check_parity("m2", 100.0, 150.0, report).passed
    assert any(e.reason_code.value == "NUMERIC_MISMATCH" for e in report.exceptions)


def _stub_llm(prompt: str) -> str:
    return "CALCULATE(SUM(Sales[Amount]), Sales[Year] = 2024)"


def test_agent_rewrite_is_never_auto():
    kb = KnowledgeBase()
    kb.init_from_seeds()
    repo = MappingRepository()
    repo.init_from_seeds()
    assistant = MigrationAssistant(_stub_llm, kb, repo)
    failed = ConversionDecision(
        source_tool="qlikview",
        source_artifact="app/expr1",
        source_expression="Sum({<Year={$(vYear)}>} Amount)",
        target_expression=None,
        tier=ConfidenceTier.MANUAL,
        confidence=0.0,
        fallback_strategy="manual",
    )
    rewritten = assistant.rewrite_failed_conversion(failed)
    assert rewritten.tier == ConfidenceTier.ASSISTED
    assert rewritten.produced_by_agent
    assert rewritten.confidence <= 0.89
    assert assistant.diff_log  # diff is logged
    assistant.assert_not_auto(rewritten)

    forged = rewritten.model_copy(update={"tier": ConfidenceTier.AUTO})
    with pytest.raises(AgentGuardrailError):
        assistant.assert_not_auto(forged)
    kb.close()
    repo.close()


def test_agent_explanation_cites_repository():
    kb = KnowledgeBase()
    kb.init_from_seeds()
    repo = MappingRepository()
    repo.init_from_seeds()
    captured = {}

    def capturing_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return "explained"

    assistant = MigrationAssistant(capturing_llm, kb, repo)
    decision = ConversionDecision(
        source_tool="qlikview",
        source_artifact="a/x",
        source_expression="Sum({<Year={2024}>} Sales)",
        target_expression="CALCULATE(SUM(Sales[Sales]), Sales[Year] = 2024)",
        tier=ConfidenceTier.ASSISTED,
        confidence=0.85,
        rule_id="qlik-set-field-value",
        feature_mapping="Set analysis",
    )
    assistant.explain_decision(decision)
    assert "qlik-set-field-value" in captured["prompt"]
    assert "FeatureMapping 'Set analysis'" in captured["prompt"]
    kb.close()
    repo.close()
