import pytest

from bimigrate.convert import ConversionEngine
from bimigrate.convert.expr_parser import parse_expression
from bimigrate.convert.loadscript_m import LoadScriptConverter
from bimigrate.kb import KnowledgeBase
from bimigrate.models.core import ConfidenceTier

from .conftest import QVS_SCRIPT


@pytest.fixture(scope="module")
def engine():
    kb = KnowledgeBase()
    kb.init_from_seeds()
    resolver = lambda name: f"Sales[{name}]"  # noqa: E731
    return ConversionEngine(kb, field_resolver=resolver, table_hint="Sales")


# -- Tableau ----------------------------------------------------------------


def test_tableau_simple_aggregate_is_auto(engine):
    d = engine.convert_expression("tableau", "SUM([Sales]) / SUM([Quantity])")
    assert d.tier == ConfidenceTier.AUTO
    assert d.target_expression == "SUM(Sales[Sales]) / SUM(Sales[Quantity])"
    assert d.rule_id  # traceability: cites a rule


def test_tableau_if_then_else(engine):
    d = engine.convert_expression(
        "tableau", "IF [Sales] > 100 THEN 'High' ELSEIF [Sales] > 50 THEN 'Mid' ELSE 'Low' END"
    )
    assert d.target_expression.startswith("SWITCH(TRUE()")
    assert '"High"' in d.target_expression


def test_tableau_lod_fixed(engine):
    d = engine.convert_expression("tableau", "{ FIXED [Customer] : SUM([Sales]) }")
    assert "ALLEXCEPT('Sales', Sales[Customer])" in d.target_expression
    assert d.tier == ConfidenceTier.ASSISTED
    assert "tab-lod-fixed" in (d.rule_id or "")


def test_tableau_nested_lod_capped_assisted(engine):
    d = engine.convert_expression("tableau", "{ FIXED [A] : SUM({ INCLUDE [B] : MAX([X]) }) }")
    assert d.tier == ConfidenceTier.ASSISTED
    assert d.confidence <= 0.62
    assert any("nested LOD" in a for a in d.annotations)


def test_tableau_zn_ifnull(engine):
    d = engine.convert_expression("tableau", "ZN(SUM([Sales]))")
    assert d.target_expression == "COALESCE(SUM(Sales[Sales]), 0)"
    assert d.tier == ConfidenceTier.AUTO


def test_tableau_datediff(engine):
    d = engine.convert_expression("tableau", "DATEDIFF('month', [Start], [End])")
    assert d.target_expression == "DATEDIFF(Sales[Start], Sales[End], MONTH)"


def test_tableau_unknown_function_manual(engine):
    d = engine.convert_expression("tableau", "FROBNICATE([Sales])")
    assert d.tier == ConfidenceTier.MANUAL
    assert d.target_expression is None
    assert d.fallback_strategy


# -- Qlik ------------------------------------------------------------------


def test_qlik_set_analysis_field_value(engine):
    d = engine.convert_expression("qlikview", "Sum({<Year={2024}>} Sales)")
    assert d.target_expression == "CALCULATE(SUM(Sales[Sales]), Sales[Year] = 2024)"
    assert d.tier == ConfidenceTier.ASSISTED


def test_qlik_set_exclusion_and_clear(engine):
    d = engine.convert_expression("qlikview", "Sum({<Region-={'EU'}, Year=>} Sales)")
    assert 'Sales[Region] <> "EU"' in d.target_expression
    assert "REMOVEFILTERS(Sales[Year])" in d.target_expression


def test_qlik_alternate_state_is_manual(engine):
    d = engine.convert_expression("qlikview", "Sum({State1<Year={2024}>} Sales)")
    assert d.tier == ConfidenceTier.MANUAL
    assert any("alternate state" in a for a in d.annotations)
    assert d.fallback_strategy


def test_qlik_aggr(engine):
    d = engine.convert_expression("qlikview", "Avg(Aggr(Sum(Sales), Customer))")
    assert "SUMMARIZE" in d.target_expression
    assert d.tier == ConfidenceTier.ASSISTED


def test_qlik_total_qualifier(engine):
    d = engine.convert_expression("qlikview", "Sum(TOTAL Sales)")
    assert d.target_expression == "CALCULATE(SUM(Sales[Sales]), ALLSELECTED())"


def test_qlik_count_distinct(engine):
    d = engine.convert_expression("qliksense", "Count(DISTINCT CustomerID)")
    assert d.target_expression == "DISTINCTCOUNT(Sales[CustomerID])"
    assert d.tier == ConfidenceTier.AUTO


# -- Spotfire ------------------------------------------------------------------


def test_spotfire_over_allprevious(engine):
    d = engine.convert_expression("spotfire", "Sum([Amount]) OVER (AllPrevious([OrderDate]))")
    assert "FILTER(ALLSELECTED(Sales[OrderDate])" in d.target_expression
    assert d.tier == ConfidenceTier.ASSISTED


def test_spotfire_case_when(engine):
    d = engine.convert_expression("spotfire", "CASE WHEN [Amount] > 100 THEN 'High' ELSE 'Low' END")
    assert d.target_expression.startswith("SWITCH(TRUE()")


def test_spotfire_sn(engine):
    d = engine.convert_expression("spotfire", "SN([Amount], 0)")
    assert d.target_expression == "COALESCE(Sales[Amount], 0)"
    assert d.tier == ConfidenceTier.AUTO


# -- AST/emitter internals -------------------------------------------------------


def test_parser_rejects_garbage():
    from bimigrate.convert.expr_parser import ExpressionParseError

    with pytest.raises(ExpressionParseError):
        parse_expression("SUM([Sales", "tableau")


def test_parse_failure_never_raises_from_engine(engine):
    d = engine.convert_expression("tableau", "IF [x] THEN")  # unterminated
    assert d.tier == ConfidenceTier.MANUAL
    assert any("parse error" in a for a in d.annotations)


# -- load script -> M -----------------------------------------------------------


def test_loadscript_conversion():
    result = LoadScriptConverter().convert(QVS_SCRIPT)
    names = [q.name for q in result.queries]
    assert "Sales" in names and "Customers" in names
    sales = next(q for q in result.queries if q.name == "Sales")
    assert "let" in sales.m_code and "in" in sales.m_code
    assert any("QVD" in a for a in sales.annotations)
    assert "Table.SelectRows" in sales.m_code  # WHERE clause
    assert any("SECTION ACCESS" in a for a in result.annotations)
    assert any("STORE INTO" in a for a in result.annotations)
    assert result.conversion_rate > 0.5


def test_loadscript_unconverted_blocks_reported():
    script = "SUB DoStuff\nLOAD 1 AS x AUTOGENERATE 1;\nEND SUB;\nCALL DoStuff;"
    result = LoadScriptConverter().convert(script)
    assert any(u["kind"] == "sub" for u in result.unconverted)
