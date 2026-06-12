from pathlib import Path

from bimigrate.kb import KnowledgeBase
from bimigrate.mapping import MappingRepository


def test_kb_init_counts():
    kb = KnowledgeBase()
    counts = kb.init_from_seeds()
    # 4.2 of the spec: rules are data; volume grows toward the 500+ target
    assert counts["conversion_rules"] >= 300
    assert counts["functions"] >= 300
    stats = kb.stats()
    assert stats["conversion_rules"] == len(
        {
            r.rule_id
            for tool in ("tableau", "spotfire", "qlikview", "qliksense")
            for r in kb.rules_for_tool(tool)
        }
    )
    kb.close()


def test_kb_runtime_lookup_no_hardcoding():
    kb = KnowledgeBase()
    kb.init_from_seeds()
    row = kb.lookup_function("tableau", "zn")
    assert row["dax_template"] == "COALESCE({0}, 0)"
    assert kb.is_valid_target_function("dax", "CALCULATE")
    assert not kb.is_valid_target_function("dax", "NOT_A_FUNCTION")
    assert kb.is_valid_target_function("m", "Table.SelectRows")
    rule = kb.get_rule("qlik-alternate-state")
    assert rule.tier == "MANUAL"
    kb.close()


def test_kb_exports(tmp_path: Path):
    kb = KnowledgeBase()
    kb.init_from_seeds()
    json_files = kb.export_json(tmp_path / "kb")
    assert len(json_files) == 3
    md = kb.export_markdown(tmp_path / "kb")
    assert "tableau functions" in md.read_text()
    kb.close()


def test_mapping_repository(tmp_path: Path):
    repo = MappingRepository()
    count = repo.init_from_seeds()
    assert count >= 100
    lod = repo.get("tableau", "LOD FIXED")
    assert lod.powerbi_equivalent.startswith("CALCULATE")
    unsupported = repo.unsupported("qlikview")
    names = {m.feature_name for m in unsupported}
    assert "Macros (VBScript)" in names
    assert "Alternate states" in names
    assert all(m.fallback_strategy for m in unsupported)

    excel = repo.export_excel(tmp_path / "matrix.xlsx")
    assert excel.exists()
    json_path = repo.export_json(tmp_path / "matrix.json")
    assert json_path.exists()
    repo.close()
