"""Regressions found by running against a real Power BI dashboard.

Each test here encodes a bug that synthetic fixtures did not catch:

1. Visual `Where` clauses live in the container's executed `query`, not in
   `prototypeQuery` — a field filtered there and projected nowhere read as
   unused.
2. Analytics transforms (forecast, clustering) emit `TransformTableRef`
   pseudo-columns that are not model objects and must not be reported as
   broken references.
3. Protected objects (hierarchy levels, relationship endpoints, …) did not
   seed the reachability walk, so anything *they* depended on read as
   unused — e.g. a calculated column referenced only by another calculated
   column that a hierarchy keeps alive.
4. Extractors publish VertiPaq's internal `H$`/`U$`/`R$` structures
   alongside real tables; those are storage artifacts, not inventory.
"""

import json

from pbi_lineage.connectors.dmv import read_model_via_dmv
from pbi_lineage.graph import nid_column
from pbi_lineage.readers.layout_legacy import parse_layout
from pbi_lineage.readers.pbix import read_pbix
from pbi_lineage.resolve import STATUS_USED, analyze_model
from pbi_lineage.schema import (
    Column,
    Hierarchy,
    HierarchyLevel,
    Model,
    RefKind,
    Table,
)

from .pbil_fixtures import alias_ref, layout, section, visual_container, write_pbix


def _query_command(select, where=None):
    """The shape Power BI writes into visualContainer.query."""
    query = {
        "Version": 2,
        "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
        "Select": select,
    }
    if where is not None:
        query["Where"] = where
    return json.dumps({"Commands": [{"SemanticQueryDataShapeCommand": {"Query": query}}]})


def test_query_where_clause_is_extracted():
    """Bug 1: a column filtered only in the executed query must be found."""
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    container["query"] = _query_command(
        select=[{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        where=[
            {
                "Condition": {
                    "In": {
                        "Expressions": [{"Column": {"Expression": alias_ref("c"), "Property": "Year"}}],
                        "Values": [[{"Literal": {"Value": "'2021'"}}]],
                    }
                }
            }
        ],
    )
    report = parse_layout(layout(sections=[section(visual_containers=[container])]))
    refs = report.pages[0].visuals[0].references
    filtered = {(r.table, r.name) for r in refs if r.scope == "visual:filter"}
    assert ("Sales", "Year") in filtered, "query-level Where filter was not extracted"


def test_filter_only_column_is_not_reported_unused():
    """Bug 1, end to end: the whole point of extracting the Where clause."""
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    container["query"] = _query_command(
        select=[{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        where=[{"Condition": {"Column": {"Expression": alias_ref("c"), "Property": "Year"}}}],
    )
    report = parse_layout(layout(sections=[section(visual_containers=[container])]))
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty"), Column(name="Year")])])
    result = analyze_model(model, [report])
    assert result.verdicts[nid_column("Sales", "Year")].status == STATUS_USED


def test_transform_outputs_are_not_model_references():
    """Bug 2: forecast/cluster outputs must not become broken references."""
    container = visual_container(visual_type="lineChart")
    container["query"] = json.dumps(
        {
            "Commands": [
                {
                    "SemanticQueryDataShapeCommand": {
                        "Query": {
                            "Version": 2,
                            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
                            "Select": [
                                {
                                    "Column": {
                                        "Expression": {"TransformTableRef": {"Source": "output0"}},
                                        "Property": "forecastValue",
                                    }
                                },
                                {
                                    "Column": {
                                        "Expression": {"TransformTableRef": {"Source": "output0"}},
                                        "Property": "confidenceHighBound",
                                    }
                                },
                                {"Column": {"Expression": alias_ref("c"), "Property": "Qty"}},
                            ],
                        }
                    }
                }
            ]
        }
    )
    report = parse_layout(layout(sections=[section(visual_containers=[container])]))
    names = {r.name for r in report.pages[0].visuals[0].references}
    assert "Qty" in names
    assert "forecastValue" not in names
    assert "confidenceHighBound" not in names

    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty")])])
    result = analyze_model(model, [report])
    assert result.unresolved == [], "transform outputs leaked in as broken references"


def test_protected_object_seeds_reachability():
    """Bug 3: a hierarchy keeps Quarter alive; Quarter's DAX keeps QuarterNo
    alive, and QuarterNo's DAX keeps MonthNo alive."""
    model = Model(
        name="AutoDate",
        tables=[
            Table(
                name="DateTable",
                columns=[
                    Column(name="Date"),
                    Column(name="MonthNo", is_calculated=True, expression="MONTH([Date])"),
                    Column(name="QuarterNo", is_calculated=True, expression="INT(([MonthNo] + 2) / 3)"),
                    Column(name="Quarter", is_calculated=True, expression='"Qtr " & [QuarterNo]'),
                ],
                hierarchies=[
                    Hierarchy(
                        name="Date Hierarchy", levels=[HierarchyLevel(name="Quarter", column="Quarter")]
                    )
                ],
            )
        ],
    )
    result = analyze_model(model, [])
    assert result.verdicts[nid_column("DateTable", "Quarter")].status == STATUS_USED
    for downstream in ("QuarterNo", "MonthNo"):
        verdict = result.verdicts[nid_column("DateTable", downstream)]
        assert verdict.status == STATUS_USED, (
            f"{downstream} is consumed by a protected calculated column but read as " f"{verdict.status}"
        )


def test_internal_vertipaq_tables_are_not_inventory():
    """Bug 4: H$/U$/R$ rows are storage structures, not model tables."""

    class Executor:
        def query(self, dmv_query):
            if "TMSCHEMA_TABLES" in dmv_query:
                return [{"ID": 1, "Name": "Sales"}]
            if "TMSCHEMA_COLUMNS" in dmv_query:
                return [
                    {"ID": 10, "TableID": 1, "TableName": "Sales", "Name": "Qty", "Type": 1},
                    # real table missing from TMSCHEMA_TABLES — must be rebuilt
                    {"ID": 11, "TableID": 2, "TableName": "DateTable", "Name": "Year", "Type": 2},
                    # internal structures — must be dropped
                    {"ID": 12, "TableID": 3, "TableName": "H$Sales (12)$Qty (7)", "Name": "x", "Type": 1},
                    {"ID": 13, "TableID": 4, "TableName": "U$DateTable", "Name": "y", "Type": 1},
                    {"ID": 14, "TableID": 5, "TableName": "R$Sales (12)$abc", "Name": "z", "Type": 1},
                ]
            raise RuntimeError("not available")

    model = read_model_via_dmv(Executor(), name="M")
    assert sorted(t.name for t in model.tables) == ["DateTable", "Sales"]
    assert [c.name for c in model.table("DateTable").columns] == ["Year"]


def test_real_pbix_shape_end_to_end(tmp_path):
    """A PBIX whose only model reference sits in the query Where clause
    still analyzes cleanly through the public reader entry point."""
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        }
    )
    container["query"] = _query_command(
        select=[{"Column": {"Expression": alias_ref("c"), "Property": "Qty"}, "Name": "q"}],
        where=[{"Condition": {"Column": {"Expression": alias_ref("c"), "Property": "Year"}}}],
    )
    path = write_pbix(
        tmp_path / "r.pbix",
        layout(sections=[section(visual_containers=[container])]),
        include_datamodel=False,
    )
    result = read_pbix(path)
    fields = {(r.table, r.name, r.kind) for r in result.report.all_references()}
    assert ("Sales", "Year", RefKind.COLUMN) in fields


# ---------------------------------------------------------------------------
# Regressions found by running the analyzer over Microsoft's own sample .pbix
# corpus (github.com/microsoft/powerbi-desktop-samples).
# ---------------------------------------------------------------------------

import io  # noqa: E402
import struct  # noqa: E402
import zipfile  # noqa: E402

from pbi_lineage.readers.msection import parse_section, split_section  # noqa: E402
from pbi_lineage.readers.pbix import _extract_m_section, _mashup_package  # noqa: E402


def _mashup_container(section_text: str, *, trailing: bytes = b"") -> bytes:
    """A DataMashup part the way a real PBIX writes it: a length-prefixed
    zip followed by further length-prefixed sections."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("Config/Package.xml", "<Package/>")
        package.writestr("Formulas/Section1.m", section_text)
    body = buffer.getvalue()
    return struct.pack("<II", 0, len(body)) + body + trailing


SECTION = 'section Section1;\n\nshared Sales = let\n    Source = Excel.Workbook(File.Contents("C:\\x.xlsx"))\nin\n    Source;\n'


def test_mashup_zip_is_read_by_its_length_not_by_scanning_to_the_end():
    """The trailing sections contain bytes that `zipfile` mistakes for an
    end-of-central-directory record; it then reports an archive with no
    entries and *no error*, which silently costs every query in the file."""
    trailing = b"PK\x05\x06" + b"\x00" * 40  # a decoy EOCD in the permissions section
    raw = _mashup_container(SECTION, trailing=trailing)

    naive = raw[raw.find(b"PK\x03\x04") :]
    with zipfile.ZipFile(io.BytesIO(naive)) as archive:
        assert archive.namelist() == [], "the decoy must actually fool the naive read"

    warnings: list[str] = []
    assert _extract_m_section(raw, warnings) is not None
    assert warnings == []


def test_mashup_package_falls_back_when_the_length_prefix_is_absent():
    raw = _mashup_container(SECTION)[8:]  # a container written without the prefix
    warnings: list[str] = []
    assert _extract_m_section(raw, warnings) is not None


def test_section_split_ignores_semicolons_inside_strings_and_comments():
    text = (
        'section Section1;\n'
        'shared A = "a;b";\n'
        '// a comment with ; in it\n'
        'shared B = 1;\n'
    )
    names = [q.name for q in parse_section(text)]
    assert names == ["A", "B"]
    assert len(split_section(text)) == 3  # section, A, and B (the comment rides with B)


def test_section_parses_quoted_names_parameters_and_functions():
    text = (
        'section Section1;\n'
        'shared #"My Table" = let Source = 1 in Source;\n'
        'shared FileLocation = "C:\\Data" meta [IsParameterQuery=true];\n'
        'shared Add = (a, b) => a + b;\n'
    )
    queries = {q.name: q for q in parse_section(text)}
    assert set(queries) == {"My Table", "FileLocation", "Add"}
    assert queries["FileLocation"].is_parameter
    assert queries["FileLocation"].expression == '"C:\\Data"', "the meta record is not the value"
    assert queries["Add"].is_function


def test_the_section_document_supplies_power_query_the_partition_lacks():
    """The partition of such a file holds `SELECT * FROM [Sales]` — the M
    engine's wrapper, not the query. Treating that as the query is what
    makes a model look like it has no data source at all."""
    from pbi_lineage.readers.pbix import _merge_m_section  # noqa: PLC0415
    from pbi_lineage.schema import Model, Partition, Table  # noqa: PLC0415

    model = Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                partitions=[Partition(name="p", source_type="query", expression="SELECT * FROM [Sales]")],
            )
        ],
    )
    _merge_m_section(model, SECTION + 'shared Param = "x";\n', [])
    partition = model.tables[0].partitions[0]
    assert partition.source_type == "m"
    assert "Excel.Workbook" in partition.expression
    assert [e.name for e in model.expressions] == ["Param"]


def test_a_partition_that_already_holds_real_m_is_not_overwritten():
    from pbi_lineage.readers.pbix import _merge_m_section  # noqa: PLC0415
    from pbi_lineage.schema import Model, Partition, Table  # noqa: PLC0415

    real = 'let Source = Sql.Database("a","b") in Source'
    model = Model(
        name="M",
        tables=[Table(name="Sales", partitions=[Partition(name="p", source_type="m", expression=real)])],
    )
    _merge_m_section(model, SECTION, [])
    assert model.tables[0].partitions[0].expression == real


# ---------------------------------------------------------------------------
# Reference resolution and the "why is this used" sentence
# ---------------------------------------------------------------------------

from pbi_lineage.graph import describe_consumer, nid_hierarchy  # noqa: E402
from pbi_lineage.resolve import STATUS_UNUSED, STATUS_USED, analyze_model  # noqa: E402
from pbi_lineage.schema import (  # noqa: E402
    Column,
    FieldReference,
    Hierarchy,
    HierarchyLevel,
    Measure,
    Model,
    Page,
    RefKind,
    ReportLayout,
    Table,
    Visual,
)


def _ref(name, *, table=None, kind=RefKind.COLUMN, scope="visual:projection"):
    return FieldReference(table=table, name=name, kind=kind, scope=scope, evidence="sections[0]…")


def _report(refs, *, title=None, visual_type="barChart", page="Overview"):
    return ReportLayout(
        name="Rep",
        pages=[Page(name=page, visuals=[Visual(name="v1", visual_type=visual_type, title=title, references=refs)])],
    )


def test_an_unqualified_measure_reference_still_counts_as_usage():
    """Report filters carry measures with no table. Dropping them made the
    columns behind the measure read as unused."""
    model = Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                columns=[Column(name="Qty")],
                measures=[Measure(name="Total", expression="SUM(Sales[Qty])")],
            )
        ],
    )
    result = analyze_model(model, [_report([_ref("Total")])])
    assert result.verdicts["measure:Total"].status == STATUS_USED
    assert result.verdicts[nid_column("Sales", "Qty")].status == STATUS_USED
    assert not result.unresolved


def test_an_unqualified_column_is_resolved_only_when_it_is_unambiguous():
    model = Model(
        name="M",
        tables=[
            Table(name="A", columns=[Column(name="Shared"), Column(name="OnlyHere")]),
            Table(name="B", columns=[Column(name="Shared")]),
        ],
    )
    result = analyze_model(model, [_report([_ref("OnlyHere"), _ref("Shared")])])
    assert result.verdicts[nid_column("A", "OnlyHere")].status == STATUS_USED
    assert result.verdicts[nid_column("A", "Shared")].status == STATUS_UNUSED
    assert [r.name for r in result.unresolved] == ["Shared"], "ambiguity is not evidence for either"


def test_an_unqualified_name_matching_a_table_prefers_that_table():
    model = Model(
        name="M",
        tables=[
            Table(name="Product", columns=[Column(name="Product")]),
            Table(name="Customer", columns=[Column(name="Product")]),
        ],
    )
    result = analyze_model(model, [_report([_ref("Product")])])
    assert result.verdicts[nid_column("Product", "Product")].status == STATUS_USED
    assert result.verdicts[nid_column("Customer", "Product")].status == STATUS_UNUSED


def test_hidden_auto_date_tables_do_not_make_a_name_ambiguous():
    """A model with date columns carries a hidden LocalDateTable per date,
    each with the same column names. Letting those compete would leave the
    author's own column unresolved."""
    model = Model(
        name="M",
        tables=[
            Table(name="Calendar", columns=[Column(name="Month")]),
            Table(name="LocalDateTable_abc", columns=[Column(name="Month")]),
            Table(name="LocalDateTable_def", columns=[Column(name="Month")]),
        ],
    )
    result = analyze_model(model, [_report([_ref("Month")])])
    assert result.verdicts[nid_column("Calendar", "Month")].status == STATUS_USED


def test_an_auto_date_hierarchy_reference_marks_every_candidate():
    """`Date Hierarchy.Month` names no table and every candidate is
    generated. Marking them all is the conservative reading; it can never
    produce a false Unused, and the evidence says the reference did not say
    which."""
    levels = [HierarchyLevel(name="Month", column="Month")]
    model = Model(
        name="M",
        tables=[
            Table(
                name=f"LocalDateTable_{suffix}",
                columns=[Column(name="Month")],
                hierarchies=[Hierarchy(name="Date Hierarchy", levels=levels)],
            )
            for suffix in ("abc", "def")
        ],
    )
    result = analyze_model(model, [_report([_ref("Date Hierarchy.Month", kind=RefKind.HIERARCHY_LEVEL)])])
    for suffix in ("abc", "def"):
        assert result.verdicts[nid_hierarchy(f"LocalDateTable_{suffix}", "Date Hierarchy")].status == STATUS_USED
    assert not result.unresolved
    evidence = result.graph.in_edges(nid_hierarchy("LocalDateTable_abc", "Date Hierarchy"))[0].evidence
    assert "names no table" in evidence


def test_a_reference_to_a_table_that_exists_and_lacks_it_is_still_broken():
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Date")])])
    result = analyze_model(model, [_report([_ref("Dates", table="Sales")])])
    assert [r.name for r in result.unresolved] == ["Dates"]


def test_why_a_column_is_used_names_the_visual_and_page_not_a_json_path():
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty")])])
    report = _report([_ref("Qty", table="Sales")], title="Sales by month", visual_type="barChart")
    verdict = analyze_model(model, [report]).verdicts[nid_column("Sales", "Qty")]
    assert verdict.reasons[0] == 'shown in the barChart “Sales by month” on page “Overview”'
    assert verdict.evidence, "the raw location is still recorded as evidence"


def test_a_visual_with_no_typed_title_is_named_by_its_type():
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty")])])
    verdict = analyze_model(model, [_report([_ref("Qty", table="Sales")])]).verdicts[nid_column("Sales", "Qty")]
    assert verdict.reasons[0] == "shown in the barChart on page “Overview”"


def test_a_protection_reason_is_never_dropped_for_a_consumer_sentence():
    """"Kept because it is a sortByColumn target" appears in no in-edge, and
    it is exactly what someone needs before deleting the thing."""
    model = Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                columns=[Column(name="Month", sort_by_column="MonthNo"), Column(name="MonthNo")],
            )
        ],
    )
    verdict = analyze_model(model, [_report([_ref("MonthNo", table="Sales")])]).verdicts[
        nid_column("Sales", "MonthNo")
    ]
    assert "sortByColumn" in verdict.reasons[0]
    assert any("shown in" in reason for reason in verdict.reasons)


# ---------------------------------------------------------------------------
# Findings that a person can act on
# ---------------------------------------------------------------------------

from pbi_lineage.mindex import MExpressionIndex  # noqa: E402
from pbi_lineage.rules import EstateContext, run_rules  # noqa: E402


def _findings(model, reports):
    index = MExpressionIndex()
    index.add_model(model)
    return run_rules(
        EstateContext(model=model, analysis=analyze_model(model, reports), reports=reports, m_index=index)
    )


def test_one_finding_per_missing_field_not_per_occurrence():
    """The same missing field in four hundred visuals is one thing to fix."""
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty")])])
    refs = [_ref("Gone", table="Sales") for _ in range(40)]
    broken = [f for f in _findings(model, [_report(refs)]) if f.rule_id == "broken-visual-reference"]
    assert len(broken) == 1
    assert "40 places" in broken[0].message
    assert broken[0].detail["occurrences"] == 40


def test_a_reference_to_an_auto_date_table_is_not_reported_as_broken():
    model = Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty")])])
    findings = _findings(model, [_report([_ref("Date", table="LocalDateTable_abc")])])
    kinds = {f.rule_id for f in findings}
    assert "auto-date-reference" in kinds
    assert "broken-visual-reference" not in kinds


def test_generated_calculated_columns_do_not_raise_errors_nobody_can_fix():
    """Power BI wrote both the column and the column it reads; what is
    missing is a row the extractor did not return."""
    model = Model(
        name="M",
        tables=[
            Table(
                name="LocalDateTable_abc",
                columns=[Column(name="Year", expression="YEAR([Date])")],
            )
        ],
    )
    findings = _findings(model, [])
    assert not [f for f in findings if f.rule_id == "broken-dax"]
    info = [f for f in findings if f.rule_id == "auto-date-reference"]
    assert info and "gap in the read" in info[0].message


def test_a_bare_column_reference_in_dax_resolves_when_it_is_unambiguous():
    from pbi_lineage import dax  # noqa: PLC0415

    model = Model(
        name="M",
        tables=[
            Table(name="Calendar", columns=[Column(name="Month")]),
            Table(name="LocalDateTable_abc", columns=[Column(name="Month")]),
        ],
    )
    resolution = dax.resolve_refs(dax.analyze("SUM([Month])"), model)
    assert not resolution.unknown
    assert ("column", "Calendar", "Month") in [(r.kind, r.table, r.name) for r in resolution.refs]


def test_a_bare_column_reference_stays_unknown_when_two_authored_tables_have_it():
    from pbi_lineage import dax  # noqa: PLC0415

    model = Model(
        name="M",
        tables=[
            Table(name="A", columns=[Column(name="Month")]),
            Table(name="B", columns=[Column(name="Month")]),
        ],
    )
    resolution = dax.resolve_refs(dax.analyze("SUM([Month])"), model)
    assert [r.name for r in resolution.unknown] == ["Month"]


def test_an_implicit_measure_finding_names_the_visual_and_says_it_once():
    model = Model(
        name="M",
        tables=[Table(name="Sales", columns=[Column(name="Qty", summarize_by="sum")])],
    )
    report = _report([_ref("Qty", table="Sales"), _ref("Qty", table="Sales")], title="Revenue")
    implicit = [f for f in _findings(model, [report]) if f.rule_id == "implicit-measure"]
    assert len(implicit) == 1
    assert 'the barChart “Revenue” on page “Overview”' in implicit[0].message


# ---------------------------------------------------------------------------
# The model-read completeness gate
# ---------------------------------------------------------------------------

from pbi_lineage.removal import plan_removal  # noqa: E402


def _partial_model():
    """A model whose reader returned Sales but not the Budget table the
    report also uses — what the offline extractor really does."""
    return Model(name="M", tables=[Table(name="Sales", columns=[Column(name="Qty"), Column(name="Spare")])])


def test_a_table_the_report_uses_but_the_reader_missed_is_recorded():
    result = analyze_model(_partial_model(), [_report([_ref("Amount", table="Budget")])])
    assert result.missing_tables == ["Budget"]
    assert not result.model_read_complete


def test_auto_date_tables_do_not_count_as_an_incomplete_read():
    """Their absence says nothing about whether the authored model was read
    whole, and the user cannot act on it either way."""
    result = analyze_model(_partial_model(), [_report([_ref("Date", table="LocalDateTable_abc")])])
    assert result.missing_tables == []
    assert result.model_read_complete


def test_removal_is_blocked_while_the_model_is_only_partly_read():
    """A missing table takes its measures and their DAX with it, so whatever
    those consumed reads as unused. Scripting a delete off that is the
    mistake this tool exists to prevent."""
    model = _partial_model()
    result = analyze_model(model, [_report([_ref("Qty", table="Sales"), _ref("Amount", table="Budget")])])
    spare = nid_column("Sales", "Spare")
    assert result.verdicts[spare].status == STATUS_UNUSED

    plan = plan_removal(
        result.graph, result.verdicts, [spare], model=model, missing_tables=result.missing_tables
    )
    assert plan.blocked
    assert any("not read whole" in reason for reason in plan.block_reasons)


def test_removal_proceeds_when_the_model_was_read_whole():
    model = _partial_model()
    result = analyze_model(model, [_report([_ref("Qty", table="Sales")])])
    plan = plan_removal(
        result.graph,
        result.verdicts,
        [nid_column("Sales", "Spare")],
        model=model,
        missing_tables=result.missing_tables,
    )
    assert not plan.blocked


def test_the_incomplete_read_outranks_everything_else_in_the_findings():
    model = _partial_model()
    findings = _findings(model, [_report([_ref("Amount", table="Budget")])])
    incomplete = [f for f in findings if f.rule_id == "incomplete-model-read"]
    assert len(incomplete) == 1
    assert "Budget" in incomplete[0].message
    assert "partial read" in incomplete[0].message
