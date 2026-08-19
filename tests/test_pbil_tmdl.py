"""TMDL parser unit tests, including a differential test against the
in-repo bimigrate TMDL emitter (spec §13: cross-check independent
implementations and triage disagreements)."""

from pathlib import Path

from pbi_lineage.readers.tmdl import parse_tmdl, read_definition_dir


def test_quoted_names_and_escaped_quotes():
    nodes = parse_tmdl("table 'Bob''s Table'\n\tcolumn 'A B'\n\t\tdataType: string\n")
    assert nodes[0].name == "Bob's Table"
    assert nodes[0].children[0].name == "A B"


def test_property_vs_declaration_disambiguation():
    # `column: Region` inside a level is a property, not a column declaration
    text = (
        "table T\n"
        "\thierarchy H\n"
        "\t\tlevel L\n"
        "\t\t\tcolumn: Region\n"
        "\tcolumn Region\n"
        "\t\tdataType: string\n"
    )
    nodes = parse_tmdl(text)
    table = nodes[0]
    hierarchy = table.children[0]
    level = hierarchy.children[0]
    assert level.keyword == "level" and level.properties["column"] == "Region"
    assert table.children[1].keyword == "column" and table.children[1].name == "Region"


def test_roles_and_expressions(tmp_path):
    definition = tmp_path / "M.SemanticModel" / "definition"
    (definition / "roles").mkdir(parents=True)
    (definition / "tables").mkdir()
    (definition / "tables" / "T.tmdl").write_text("table T\n\tcolumn C\n\t\tdataType: string\n")
    (definition / "roles" / "Managers.tmdl").write_text(
        "role Managers\n" "\tmodelPermission: read\n" '\ttablePermission T = [C] = "EMEA"\n'
    )
    (definition / "expressions.tmdl").write_text('expression Param1 = "dev" meta [IsParameterQuery=true]\n')
    warnings: list[str] = []
    model = read_definition_dir(definition, warnings)
    assert model.roles[0].name == "Managers"
    # RLS expressions make columns used (§5.3) — they must survive parsing
    assert model.roles[0].table_permissions["T"] == '[C] = "EMEA"'
    assert model.expressions[0].name == "Param1"
    assert "IsParameterQuery" in model.expressions[0].expression


def test_unknown_properties_are_tolerated():
    text = (
        "table T\n"
        "\tlineageTag: 1234-abcd\n"
        "\tcolumn C\n"
        "\t\tdataType: string\n"
        "\t\tlineageTag: 9876\n"
        "\t\tannotation SummarizationSetBy = Automatic\n"
    )
    nodes = parse_tmdl(text)
    column = nodes[0].children[0]
    assert column.name == "C"
    assert column.properties["dataType"] == "string"


def test_differential_against_bimigrate_emitter(tmp_path: Path):
    """The in-repo emitter and this reader are independent TMDL
    implementations — writing with one and reading with the other must
    round-trip the inventory."""
    from bimigrate.emit.tmdl import (
        TmdlColumn,
        TmdlMeasure,
        TmdlModel,
        TmdlRelationship,
        TmdlRole,
        TmdlTable,
    )

    emitted = TmdlModel(
        name="RoundTrip",
        tables=[
            TmdlTable(
                name="Fact Sales",
                columns=[
                    TmdlColumn(name="Amount", data_type="double"),
                    TmdlColumn(name="HiddenKey", data_type="int64", is_hidden=True),
                ],
                measures=[
                    TmdlMeasure(name="Total", expression="SUM('Fact Sales'[Amount])", format_string="#,0"),
                    TmdlMeasure(name="Multi", expression="VAR a = 1\nRETURN a", display_folder="KPIs"),
                ],
                partition_m='let Source = Csv.Document("f") in Source',
            ),
            TmdlTable(name="Dim Region", columns=[TmdlColumn(name="Region")]),
        ],
        relationships=[
            TmdlRelationship(
                from_table="Fact Sales",
                from_column="HiddenKey",
                to_table="Dim Region",
                to_column="Region",
                cross_filter="bothDirections",
            )
        ],
        roles=[TmdlRole(name="EMEA", table_filters={"Dim Region": '[Region] = "EMEA"'})],
    )
    definition_dir = tmp_path / "RT.SemanticModel" / "definition"
    emitted.write(definition_dir)
    # bimigrate writes roles under roles/ only via write(); confirm layout
    assert (definition_dir / "tables").is_dir()

    warnings: list[str] = []
    model = read_definition_dir(definition_dir, warnings)

    assert model.name == "RoundTrip"
    assert sorted(t.name for t in model.tables) == ["Dim Region", "Fact Sales"]
    fact = model.table("Fact Sales")
    assert [c.name for c in fact.columns] == ["Amount", "HiddenKey"]
    assert fact.columns[1].is_hidden
    assert [m.name for m in fact.measures] == ["Total", "Multi"]
    assert fact.measures[0].expression == "SUM('Fact Sales'[Amount])"
    assert fact.measures[1].expression == "VAR a = 1\nRETURN a"
    assert fact.measures[1].display_folder == "KPIs"
    assert fact.partitions[0].source_type == "m"
    assert "Csv.Document" in fact.partitions[0].expression

    rel = model.relationships[0]
    assert (rel.from_table, rel.from_column, rel.to_table, rel.to_column) == (
        "Fact Sales",
        "HiddenKey",
        "Dim Region",
        "Region",
    )
    assert rel.cross_filtering == "bothDirections"
    assert model.roles[0].table_permissions["Dim Region"] == '[Region] = "EMEA"'
