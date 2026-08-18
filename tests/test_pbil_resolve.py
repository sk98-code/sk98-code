"""Milestones 3–4: DAX lexer, dependency graph, reachability verdicts.

The resolver's contract: no false "unused". Every §5.3-style trap here
asserts the object survives as Used (or a capped status), with evidence.
"""

from pbi_lineage import dax
from pbi_lineage.graph import nid_calc_item, nid_column, nid_hierarchy, nid_measure, nid_table
from pbi_lineage.resolve import (
    STATUS_EXTERNAL,
    STATUS_INCOMPLETE,
    STATUS_REMOVE_MANUALLY,
    STATUS_UNUSED,
    STATUS_USED,
    analyze_model,
)
from pbi_lineage.schema import (
    CalcDependency,
    CalculationGroup,
    CalculationItem,
    Column,
    FieldReference,
    Hierarchy,
    HierarchyLevel,
    Measure,
    Model,
    Page,
    RefKind,
    Relationship,
    ReportLayout,
    ReportLevelMeasure,
    Role,
    Table,
    Visual,
)

# ---------------------------------------------------------------------------
# DAX lexer
# ---------------------------------------------------------------------------


def test_dax_basic_refs():
    analysis = dax.analyze("SUM(Sales[Qty]) + [Total Margin] + COUNTROWS('Dim Region')")
    assert dax.DaxRef("Sales", "Qty") in analysis.fields
    assert dax.DaxRef(None, "Total Margin") in analysis.fields
    assert "Dim Region" in analysis.tables
    assert "SUM" in analysis.functions and "COUNTROWS" in analysis.functions


def test_dax_strings_and_comments_hide_brackets():
    expr = (
        'IF([Flag], "literal [NotARef] with ""quotes""", BLANK())\n'
        "-- comment [AlsoNotARef]\n"
        "/* block [StillNot] 'NorThis' */\n"
        "+ Sales[Real]"
    )
    analysis = dax.analyze(expr)
    names = {(r.table, r.name) for r in analysis.fields}
    assert names == {(None, "Flag"), ("Sales", "Real")}
    assert analysis.errors == []


def test_dax_var_shadowing_and_locals():
    expr = "VAR Sales = 1 VAR x = Sales + 2 RETURN x + COUNTROWS(Geo)"
    analysis = dax.analyze(expr)
    # `Sales` after VAR is a local, not a table reference; Geo is a table
    assert "Sales" not in analysis.tables
    assert "Geo" in analysis.tables


def test_dax_quoted_names_with_escapes_and_spaces():
    analysis = dax.analyze("SUM('Bob''s Sales'[Amount ]] bracket])")
    assert analysis.fields == [dax.DaxRef("Bob's Sales", "Amount ] bracket")]


def test_dax_wildcard_detection():
    analysis = dax.analyze("CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))")
    assert analysis.wildcard_measures
    assert dax.DaxRef("Date", "Date") in analysis.fields


def test_dax_resolution_qualified_measure_trap():
    # RDL trap (§5.4.5): a measure referenced as Table[Measure]
    model = _model()
    resolution = dax.resolve_refs(dax.analyze("Sales[Total Sales] + Sales[Qty]"), model)
    kinds = {(r.kind, r.name) for r in resolution.refs}
    assert ("measure", "Total Sales") in kinds
    assert ("column", "Qty") in kinds


def test_dax_bare_ref_in_calculated_column_prefers_host_column():
    model = _model()
    resolution = dax.resolve_refs(dax.analyze("[Qty] * 2"), model, host_table="Sales")
    assert resolution.refs == [dax.ResolvedRef("column", "Sales", "Qty")]


def test_dax_unknown_refs_are_reported_not_guessed():
    resolution = dax.resolve_refs(dax.analyze("[No Such Measure] + Nope[Field]"), _model())
    assert len(resolution.unknown) == 2


# ---------------------------------------------------------------------------
# Resolver fixtures
# ---------------------------------------------------------------------------


def _model() -> Model:
    return Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                columns=[
                    Column(name="Qty"),
                    Column(name="Region"),
                    Column(name="OrderDate"),
                    Column(name="DiscountCode"),
                    Column(name="MonthName", sort_by_column="MonthNo"),
                    Column(name="MonthNo"),
                    Column(name="Obsolete"),
                ],
                measures=[
                    Measure(name="Total Sales", expression="SUM(Sales[Qty])"),
                    Measure(name="Margin", expression="[Total Sales] * 0.2"),
                    Measure(name="Dead Measure", expression="SUM(Sales[Obsolete])"),
                ],
            ),
            Table(
                name="Geo",
                columns=[Column(name="Region"), Column(name="Country")],
                hierarchies=[
                    Hierarchy(name="Geo Hierarchy", levels=[HierarchyLevel(name="Region", column="Region")])
                ],
            ),
        ],
        relationships=[
            Relationship(
                name="R1", from_table="Sales", from_column="Region", to_table="Geo", to_column="Region"
            )
        ],
    )


def _visual_ref(table: str, name: str, kind: RefKind, scope: str = "visual:projection") -> FieldReference:
    return FieldReference(table=table, name=name, kind=kind, scope=scope, evidence=f"test:{table}.{name}")


def _report(visual_refs: list[FieldReference], **kwargs) -> ReportLayout:
    return ReportLayout(
        name=kwargs.get("name", "R1"),
        source_format="legacy",
        pages=[
            Page(
                name="p1",
                is_hidden=kwargs.get("hidden_page", False),
                visuals=[Visual(name="v1", visual_type="columnChart", references=visual_refs)],
            )
        ],
        report_level_measures=kwargs.get("rl_measures", []),
    )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_transitive_measure_usage():
    # visual uses Margin; Margin uses Total Sales uses Qty → all Used
    report = _report([_visual_ref("Sales", "Margin", RefKind.MEASURE)])
    result = analyze_model(_model(), [report])
    assert result.verdicts[nid_measure("Margin")].status == STATUS_USED
    assert result.verdicts[nid_measure("Total Sales")].status == STATUS_USED
    assert result.verdicts[nid_column("Sales", "Qty")].status == STATUS_USED
    assert result.verdicts[nid_table("Sales")].status == STATUS_USED
    # evidence present on every verdict that is Used via reachability
    assert result.verdicts[nid_measure("Margin")].evidence


def test_truly_unused_column_is_unused():
    report = _report([_visual_ref("Sales", "Total Sales", RefKind.MEASURE)])
    result = analyze_model(_model(), [report])
    assert result.verdicts[nid_column("Sales", "DiscountCode")].status == STATUS_UNUSED
    assert result.verdicts[nid_column("Sales", "DiscountCode")].deletable


def test_column_used_only_by_report_level_measure():
    # §5.4.8 fixture 1 — the classic false positive a naive tool makes
    rl = ReportLevelMeasure(name="Adj", table="Sales", expression="SUM(Sales[DiscountCode])")
    report = _report([_visual_ref("Sales", "Adj", RefKind.MEASURE)], rl_measures=[rl])
    result = analyze_model(_model(), [report])
    assert result.verdicts[nid_column("Sales", "DiscountCode")].status == STATUS_USED
    rl_verdict = result.verdicts[nid_measure("Adj", host_report="R1")]
    assert rl_verdict.status == STATUS_USED


def test_unused_report_level_measure_is_remove_manually():
    rl = ReportLevelMeasure(name="Orphan", table="Sales", expression="1")
    report = _report([_visual_ref("Sales", "Total Sales", RefKind.MEASURE)], rl_measures=[rl])
    result = analyze_model(_model(), [report])
    verdict = result.verdicts[nid_measure("Orphan", host_report="R1")]
    assert verdict.status == STATUS_REMOVE_MANUALLY
    assert not verdict.deletable


def test_relationship_and_sortby_and_hierarchy_columns_protected():
    result = analyze_model(_model(), [_report([])])
    # relationship endpoints (§5.3: active or inactive)
    assert result.verdicts[nid_column("Sales", "Region")].status == STATUS_USED
    assert result.verdicts[nid_column("Geo", "Region")].status == STATUS_USED
    # sort-by target
    monthno = result.verdicts[nid_column("Sales", "MonthNo")]
    assert monthno.status == STATUS_USED
    assert any("sortByColumn" in r for r in monthno.reasons)
    # Geo.Region is reached through the relationship root, with evidence
    geo_region = result.verdicts[nid_column("Geo", "Region")]
    assert any("relationship" in e for e in geo_region.evidence)


def test_rls_referenced_column_protected():
    model = _model()
    model.roles.append(Role(name="EMEA", table_permissions={"Geo": '[Country] = "DE"'}))
    result = analyze_model(model, [_report([])])
    verdict = result.verdicts[nid_column("Geo", "Country")]
    assert verdict.status == STATUS_USED
    assert any("RLS" in r for r in verdict.reasons)


def test_hidden_page_slicer_marks_used():
    report = _report([_visual_ref("Sales", "OrderDate", RefKind.COLUMN)], hidden_page=True)
    result = analyze_model(_model(), [report])
    assert result.verdicts[nid_column("Sales", "OrderDate")].status == STATUS_USED


def test_union_across_two_thin_reports():
    # §5.4.8 fixture 3: each report alone leaves something unused; union covers both
    r1 = _report([_visual_ref("Sales", "Total Sales", RefKind.MEASURE)], name="R1")
    r2 = _report([_visual_ref("Sales", "DiscountCode", RefKind.COLUMN)], name="R2")
    only_r1 = analyze_model(_model(), [r1])
    assert only_r1.verdicts[nid_column("Sales", "DiscountCode")].status == STATUS_UNUSED
    union = analyze_model(_model(), [r1, r2])
    assert union.verdicts[nid_column("Sales", "DiscountCode")].status == STATUS_USED


def test_incomplete_coverage_caps_all_unused():
    # §5.4.4 hard gate
    report = _report([_visual_ref("Sales", "Total Sales", RefKind.MEASURE)])
    result = analyze_model(_model(), [report], coverage_complete=False)
    discount = result.verdicts[nid_column("Sales", "DiscountCode")]
    assert discount.status == STATUS_INCOMPLETE
    assert not discount.deletable
    # Used stays Used — the gate caps only would-be-Unused objects
    assert result.verdicts[nid_measure("Total Sales")].status == STATUS_USED


def test_external_consumer_caps_unused():
    report = _report([_visual_ref("Sales", "Total Sales", RefKind.MEASURE)])
    result = analyze_model(_model(), [report], external_consumers=["Analyze in Excel: 3 users"])
    verdict = result.verdicts[nid_column("Sales", "DiscountCode")]
    assert verdict.status == STATUS_EXTERNAL
    assert not verdict.deletable


def test_broken_visual_reference_is_reported_not_guessed():
    report = _report([_visual_ref("Sales", "DeletedColumn", RefKind.COLUMN)])
    result = analyze_model(_model(), [report])
    assert any(r.name == "DeletedColumn" for r in result.unresolved)


def test_calc_group_wildcard_does_not_resurrect_unused_measures():
    model = _model()
    model.tables.append(
        Table(
            name="Time Intelligence",
            calculation_group=CalculationGroup(
                items=[CalculationItem(name="YTD", expression="CALCULATE(SELECTEDMEASURE())")]
            ),
        )
    )
    report = _report([_visual_ref("Sales", "Total Sales", RefKind.MEASURE)])
    result = analyze_model(model, [report])
    # Dead Measure stays unused: SELECTEDMEASURE is a wildcard, not a use
    assert result.verdicts[nid_measure("Dead Measure")].status == STATUS_UNUSED
    # but the calc group table/items are protected, never unused
    assert result.verdicts[nid_table("Time Intelligence")].status == STATUS_USED
    assert result.verdicts[nid_calc_item("Time Intelligence", "YTD")].status == STATUS_USED


def test_engine_edges_primary_and_disagreements_logged():
    model = _model()
    # engine knows Margin -> Total Sales; parser agrees. Give the engine an
    # extra edge the parser can't see (dynamic pattern) → disagreement log.
    deps = [
        CalcDependency(
            "MEASURE", "Sales", "Margin", "[Total Sales] * 0.2", "MEASURE", "Sales", "Total Sales"
        ),
        CalcDependency("MEASURE", "Sales", "Total Sales", "SUM(Sales[Qty])", "COLUMN", "Sales", "Qty"),
        CalcDependency(
            "MEASURE", "Sales", "Dead Measure", "SUM(Sales[Obsolete])", "COLUMN", "Sales", "Obsolete"
        ),
        CalcDependency("MEASURE", "Sales", "Margin", "[Total Sales] * 0.2", "COLUMN", "Sales", "OrderDate"),
    ]
    report = _report([_visual_ref("Sales", "Margin", RefKind.MEASURE)])
    result = analyze_model(model, [report], calc_deps=deps)
    # engine edge (Margin -> OrderDate) wins for reachability
    assert result.verdicts[nid_column("Sales", "OrderDate")].status == STATUS_USED
    assert any("OrderDate" in d.detail for d in result.disagreements)


def test_hierarchy_level_reference_marks_hierarchy_and_column():
    ref = FieldReference(
        table="Geo",
        name="Geo Hierarchy.Region",
        kind=RefKind.HIERARCHY_LEVEL,
        scope="visual:projection",
        evidence="test",
    )
    result = analyze_model(_model(), [_report([ref])])
    assert result.verdicts[nid_hierarchy("Geo", "Geo Hierarchy")].status == STATUS_USED
    assert result.verdicts[nid_column("Geo", "Region")].status == STATUS_USED


def test_dependency_tree_renders_with_evidence():
    report = _report([_visual_ref("Sales", "Margin", RefKind.MEASURE)])
    result = analyze_model(_model(), [report])
    tree = result.graph.dependency_tree(nid_measure("Margin"))
    rendered = result.graph.render_tree(tree)
    assert "Total Sales" in rendered and "Qty" in rendered
    lineage = result.graph.consumers_tree(nid_column("Sales", "Qty"))
    rendered_up = result.graph.render_tree(lineage)
    assert "Margin" in rendered_up and "visual" in rendered_up
