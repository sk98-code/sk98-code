"""Milestone 2: local AS connector — port discovery, External Tools
manifest, and full TMSCHEMA / CALC_DEPENDENCY ingest against a fake
executor (the live PyadomdExecutor needs Windows + ADOMD.NET)."""

import json
from pathlib import Path

from pbi_lineage.connectors import (
    discover_local_instances,
    list_databases,
    read_calc_dependencies,
    read_model_via_dmv,
    write_pbitool_manifest,
)


class FakeExecutor:
    """Canned rowsets keyed by DMV name; unknown DMVs raise like a real
    engine that lacks them."""

    def __init__(self, rowsets: dict[str, list[dict]]):
        self.rowsets = rowsets
        self.queries: list[str] = []

    def query(self, dmv_query: str) -> list[dict]:
        self.queries.append(dmv_query)
        dmv = dmv_query.split("$SYSTEM.")[-1].strip()
        if dmv not in self.rowsets:
            raise RuntimeError(f"unknown DMV {dmv}")
        return self.rowsets[dmv]


# ---------------------------------------------------------------------------
# Port discovery + manifest
# ---------------------------------------------------------------------------


def _write_port_file(workspace_root: Path, workspace: str, port: str | bytes, encoding="utf-16"):
    data_dir = workspace_root / workspace / "Data"
    data_dir.mkdir(parents=True)
    port_file = data_dir / "msmdsrv.port.txt"
    if isinstance(port, bytes):
        port_file.write_bytes(port)
    else:
        port_file.write_text(port, encoding=encoding)
    return port_file


def test_port_discovery_installer_and_store_layouts(tmp_path):
    installer_root = tmp_path / "install" / "AnalysisServicesWorkspaces"
    store_root = tmp_path / "store" / "AnalysisServicesWorkspaces"
    _write_port_file(installer_root, "AnalysisServicesWorkspace_guid1", "51423")  # UTF-16 + BOM
    _write_port_file(store_root, "ws2", "60123".encode("utf-16-le"))  # UTF-16 LE, no BOM
    _write_port_file(installer_root, "corrupt", b"\x00\xff\x00garbage")  # skipped

    instances = discover_local_instances([installer_root, store_root, tmp_path / "missing"])
    assert [i.port for i in instances] == [51423, 60123]
    assert instances[0].connection_string == "Data Source=localhost:51423"
    assert instances[0].workspace_dir.name == "AnalysisServicesWorkspace_guid1"


def test_port_discovery_no_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert discover_local_instances() == []


def test_pbitool_manifest(tmp_path):
    path = write_pbitool_manifest(
        tmp_path / "External Tools",
        name="Lineage Analyzer",
        description="Model dependency and lineage analysis",
        exe_path="C:\\Tools\\lineage.exe",
    )
    assert path.name == "lineage_analyzer.pbitool.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0"
    assert manifest["path"] == "C:\\Tools\\lineage.exe"
    # Desktop substitutes these placeholders at launch
    assert "%server%" in manifest["arguments"] and "%database%" in manifest["arguments"]


# ---------------------------------------------------------------------------
# TMSCHEMA ingest
# ---------------------------------------------------------------------------


def _model_rowsets() -> dict[str, list[dict]]:
    return {
        "TMSCHEMA_TABLES": [
            {"ID": 1, "Name": "Sales", "IsHidden": False},
            {"ID": 2, "Name": "Geo", "IsHidden": False},
            {"ID": 3, "Name": "Time Intelligence", "IsHidden": False},
        ],
        "TMSCHEMA_COLUMNS": [
            {"ID": 10, "TableID": 1, "ExplicitName": "RowNumber", "Type": 3},  # internal
            {
                "ID": 11,
                "TableID": 1,
                "ExplicitName": "Qty",
                "Type": 1,
                "ExplicitDataType": 6,
                "IsHidden": "true",  # string bool, as some clients return
                "SortByColumnID": 12,
                "SummarizeBy": 3,
                "SourceColumn": "Qty",
            },
            {
                "ID": 12,
                "TableID": 1,
                "ExplicitName": "Region",
                "Type": 1,
                "ExplicitDataType": 2,
                "SummarizeBy": 2,
                "IsKey": True,
            },
            {
                "ID": 13,
                "TableID": 1,
                "ExplicitName": "Margin",
                "Type": 2,  # calculated
                "ExplicitDataType": 8,
                "Expression": "[Total Sales] * 0.2",
            },
            {"ID": 21, "TableID": 2, "ExplicitName": "Region", "Type": 1, "ExplicitDataType": 2},
        ],
        "TMSCHEMA_MEASURES": [
            {
                "ID": 31,
                "TableID": 1,
                "Name": "Total Sales",
                "Expression": "SUM(Sales[Qty])",
                "FormatString": "#,0",
                "DisplayFolder": "KPIs",
                "FormatStringDefinitionID": 91,
            }
        ],
        "TMSCHEMA_HIERARCHIES": [{"ID": 41, "TableID": 2, "Name": "Geo Hierarchy"}],
        "TMSCHEMA_LEVELS": [
            # deliberately out of order: Ordinal must win
            {"ID": 52, "HierarchyID": 41, "Ordinal": 1, "Name": "City", "ColumnID": 21},
            {"ID": 51, "HierarchyID": 41, "Ordinal": 0, "Name": "Region", "ColumnID": 21},
        ],
        "TMSCHEMA_PARTITIONS": [
            {
                "ID": 61,
                "TableID": 1,
                "Name": "Sales-part",
                "Type": 4,
                "Mode": 1,
                "QueryDefinition": "let x=1 in x",
            }
        ],
        "TMSCHEMA_RELATIONSHIPS": [
            {
                "ID": 71,
                "FromTableID": 1,
                "FromColumnID": 12,
                "ToTableID": 2,
                "ToColumnID": 21,
                "IsActive": False,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 2,
            }
        ],
        "TMSCHEMA_ROLES": [{"ID": 81, "Name": "EMEA", "ModelPermission": 2}],
        "TMSCHEMA_TABLE_PERMISSIONS": [
            {"ID": 82, "RoleID": 81, "TableID": 2, "FilterExpression": '[Region] = "EMEA"'}
        ],
        "TMSCHEMA_EXPRESSIONS": [{"ID": 85, "Name": "Param1", "Expression": '"dev" meta []'}],
        "TMSCHEMA_CALCULATION_GROUPS": [{"ID": 86, "TableID": 3, "Precedence": 10}],
        "TMSCHEMA_CALCULATION_ITEMS": [
            {
                "ID": 88,
                "CalculationGroupID": 86,
                "Name": "YTD",
                "Expression": "CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))",
                "Ordinal": 0,
            },
            {
                "ID": 89,
                "CalculationGroupID": 86,
                "Name": "PY",
                "Expression": "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('Date'[Date]))",
                "Ordinal": 1,
                "FormatStringDefinitionID": 92,
            },
        ],
        "TMSCHEMA_FORMAT_STRING_DEFINITIONS": [
            {"ID": 91, "Expression": 'IF([Total Sales]>1e6, "0.0,,\\"M\\"", "#,0")'},
            {"ID": 92, "Expression": '"0.0%"'},
        ],
    }


def test_tmschema_full_ingest():
    executor = FakeExecutor(_model_rowsets())
    warnings: list[str] = []
    model = read_model_via_dmv(executor, name="LocalModel", warnings=warnings)

    assert model.name == "LocalModel"
    assert [t.name for t in model.tables] == ["Sales", "Geo", "Time Intelligence"]

    sales = model.table("Sales")
    # RowNumber (Type=3) is internal and must not appear in inventory
    assert [c.name for c in sales.columns] == ["Qty", "Region", "Margin"]

    qty = sales.columns[0]
    assert qty.data_type == "int64"
    assert qty.is_hidden  # string "true" normalized
    assert qty.sort_by_column == "Region"  # ID join resolved to a name
    assert qty.summarize_by == "sum"
    region = sales.columns[1]
    assert region.is_key and region.summarize_by == "none"
    margin = sales.columns[2]
    assert margin.is_calculated and margin.expression == "[Total Sales] * 0.2"

    total = sales.measures[0]
    assert total.format_string == "#,0"
    assert "1e6" in total.format_string_expression  # dynamic format joined

    geo = model.table("Geo")
    assert [lvl.name for lvl in geo.hierarchies[0].levels] == ["Region", "City"]  # Ordinal order
    assert geo.hierarchies[0].levels[0].column == "Region"

    part = sales.partitions[0]
    assert (part.source_type, part.mode, part.expression) == ("m", "import", "let x=1 in x")

    rel = model.relationships[0]
    assert (rel.from_table, rel.from_column, rel.to_table, rel.to_column) == (
        "Sales",
        "Region",
        "Geo",
        "Region",
    )
    assert not rel.is_active  # inactive relationships still count (§5.3)
    assert rel.to_cardinality == "one"
    assert rel.cross_filtering == "bothDirections"

    assert model.roles[0].model_permission == "read"
    assert model.roles[0].table_permissions["Geo"] == '[Region] = "EMEA"'
    assert model.expressions[0].name == "Param1"

    group = model.table("Time Intelligence").calculation_group
    assert group is not None and group.precedence == 10
    assert [item.name for item in group.items] == ["YTD", "PY"]
    assert "SELECTEDMEASURE()" in group.items[0].expression  # wildcard-edge marker
    assert group.items[1].format_string_expression == '"0.0%"'

    assert warnings == []


def test_missing_dmv_yields_warning_not_failure():
    rowsets = _model_rowsets()
    del rowsets["TMSCHEMA_CALCULATION_GROUPS"]
    del rowsets["TMSCHEMA_CALCULATION_ITEMS"]
    warnings: list[str] = []
    model = read_model_via_dmv(FakeExecutor(rowsets), warnings=warnings)
    assert model.table("Sales") is not None  # ingest still completes
    assert any("TMSCHEMA_CALCULATION_GROUPS" in w for w in warnings)


def test_calc_dependency_ingest_case_insensitive_keys():
    rows = [
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "Sales",
            "OBJECT": "Total Sales",
            "EXPRESSION": "SUM(Sales[Qty])",
            "REFERENCED_OBJECT_TYPE": "COLUMN",
            "REFERENCED_TABLE": "Sales",
            "REFERENCED_OBJECT": "Qty",
        },
        {
            # lowercase keys, as some providers return
            "object_type": "ROWS_ALLOWED",
            "table": "Geo",
            "object": "EMEA",
            "expression": '[Region] = "EMEA"',
            "referenced_object_type": "COLUMN",
            "referenced_table": "Geo",
            "referenced_object": "Region",
        },
    ]
    deps = read_calc_dependencies(FakeExecutor({"DISCOVER_CALC_DEPENDENCY": rows}))
    assert len(deps) == 2
    first = deps[0]
    assert (first.object_type, first.object, first.referenced_object) == (
        "MEASURE",
        "Total Sales",
        "Qty",
    )
    assert deps[1].object_type == "ROWS_ALLOWED"
    assert deps[1].referenced_table == "Geo"


def test_list_databases():
    executor = FakeExecutor(
        {"DBSCHEMA_CATALOGS": [{"CATALOG_NAME": "3f2e-guid-model"}, {"CATALOG_NAME": ""}]}
    )
    assert list_databases(executor) == ["3f2e-guid-model"]
