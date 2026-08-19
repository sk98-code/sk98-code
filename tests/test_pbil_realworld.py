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
