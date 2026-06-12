from pathlib import Path

import pytest

from bimigrate.parsers import (
    QlikSenseParser,
    QlikViewParser,
    SpotfireParser,
    TableauParser,
)
from bimigrate.parsers.base import ParserError, get_parser_for


def test_parser_registry(estate_dir: Path):
    assert isinstance(get_parser_for(estate_dir / "sales.twb"), TableauParser)
    assert isinstance(get_parser_for(estate_dir / "analysis.dxp"), SpotfireParser)
    assert isinstance(get_parser_for(estate_dir / "reload.qvs"), QlikViewParser)
    assert isinstance(get_parser_for(estate_dir / "sense_app.json"), QlikSenseParser)


def test_tableau_parser(estate_dir: Path):
    inv = TableauParser().parse(estate_dir / "sales.twb")
    assert inv.source_tool.value == "tableau"
    assert {ws.name for ws in inv.worksheets} == {"Sales by Region", "Trend"}
    assert inv.dashboards == ["Exec Dashboard"]
    kinds = {c.name: c.kind for c in inv.calculations}
    assert kinds["Profit Ratio"] == "calculated_field"
    assert kinds["Sales per Customer"] == "lod"
    assert kinds["Running Sales"] == "table_calc"
    assert inv.parameters == ["Top N"]
    assert any(s.language == "custom-sql" for s in inv.scripts)
    assert any(c.connection_type == "sqlserver" for c in inv.connections)
    assert inv.feature_flags["lod_expressions"] == 1
    assert inv.feature_flags["filter_action"] == 1
    assert any(r.kind == "datasource_filter" for r in inv.security)
    assert inv.fingerprint


def test_spotfire_parser(estate_dir: Path):
    inv = SpotfireParser().parse(estate_dir / "analysis.dxp")
    assert {ws.name for ws in inv.worksheets} >= {"Overview", "Trends"}
    visual_types = [v.visual_type for ws in inv.worksheets for v in ws.visuals]
    assert "bar" in visual_types and "pivot_table" in visual_types
    assert any(t.name == "SalesData" and "Amount" in t.columns for t in inv.tables)
    assert any(c.connection_type == "information-link" for c in inv.connections)
    langs = {s.language for s in inv.scripts}
    assert "ironpython" in langs and "terr" in langs
    assert "MaxYear" in inv.parameters
    assert inv.feature_flags["over_expressions"] >= 1


def test_spotfire_corrupt_file_isolated(estate_dir: Path):
    with pytest.raises(ParserError):
        SpotfireParser().parse(estate_dir / "corrupt.dxp")


def test_qlikview_qvs(estate_dir: Path):
    inv = QlikViewParser().parse(estate_dir / "reload.qvs")
    assert any(s.kind == "section_access" for s in inv.security)
    assert "Sales" in [t.name for t in inv.tables]
    assert any(c.connection_type == "qvd" for c in inv.connections)
    variables = {c.name for c in inv.calculations if c.kind == "variable"}
    assert "vMaxYear" in variables
    assert inv.platform_extras["qvd_writes"] == ["lib://Data/sales_clean.qvd"]


def test_qliksense_json(estate_dir: Path):
    inv = QlikSenseParser().parse(estate_dir / "sense_app.json")
    assert inv.artifact_name == "Sales Sense App"
    assert [ws.name for ws in inv.worksheets][0] == "Overview"
    measures = {c.name: c for c in inv.calculations}
    assert "Total Sales" in measures
    set_calcs = [c for c in inv.calculations if c.kind == "set_analysis"]
    assert set_calcs and "{<Year={2024}>}" in set_calcs[0].expression.replace(" ", "")
    assert inv.feature_flags["master_dimensions"] == 1
    assert any(s.kind == "section_access" for s in inv.security)
