"""PBIP / PBIR project tests: visual.json parsing, report extensions,
thin (byConnection) projects, TMDL model inventory, auto-detection."""

from pbi_lineage import detect_format, read_any, read_pbip
from pbi_lineage.schema import RefKind

from .pbil_fixtures import column_expr, entity_ref, measure_expr, pbir_visual, write_pbip


def _standard_pages() -> dict:
    return {
        "page1": {
            "page": {"name": "page1", "displayName": "Overview"},
            "visuals": {
                "v1": pbir_visual(
                    name="v1",
                    projections={
                        "Values": [measure_expr(entity_ref("Sales"), "Total Sales")],
                        "Category": [column_expr(entity_ref("Sales"), "Region")],
                    },
                    sort_fields=[column_expr(entity_ref("Sales"), "OrderDate")],
                    filters=[column_expr(entity_ref("Sales"), "Segment")],
                )
            },
        },
        "page2": {
            "page": {
                "name": "page2",
                "displayName": "Hidden tooltips",
                "visibility": "HiddenInViewMode",
                "filterConfig": {
                    "filters": [{"field": column_expr(entity_ref("Dates"), "Year"), "type": "Categorical"}]
                },
            },
            "visuals": {},
        },
    }


def test_pbir_visual_references_and_scopes(tmp_path):
    project = write_pbip(tmp_path, pages=_standard_pages())
    result = read_pbip(project)
    assert result.report is not None and result.report.source_format == "pbir"

    page1 = result.report.pages[0]
    assert page1.display_name == "Overview"
    visual = page1.visuals[0]
    by_scope = {}
    for ref in visual.references:
        by_scope.setdefault(ref.scope, []).append((ref.table, ref.name))
    assert ("Sales", "Total Sales") in by_scope["visual:projection"]
    assert ("Sales", "Region") in by_scope["visual:projection"]
    assert by_scope["visual:sort"] == [("Sales", "OrderDate")]
    assert by_scope["visual:filter"] == [("Sales", "Segment")]
    assert visual.visual_type == "columnChart"
    assert (visual.x, visual.width) == (10, 400)

    page2 = result.report.pages[1]
    assert page2.is_hidden
    assert [(r.table, r.name) for r in page2.filters] == [("Dates", "Year")]


def test_pbir_report_extensions_report_level_measures(tmp_path):
    extensions = {
        "entities": [
            {
                "name": "Sales",
                "measures": [{"name": "Adj Margin", "expression": "[Total Sales] * 0.9"}],
            }
        ]
    }
    project = write_pbip(tmp_path, pages={}, report_extensions=extensions)
    result = read_pbip(project)
    measures = result.report.report_level_measures
    assert [(m.name, m.table, m.expression) for m in measures] == [
        ("Adj Margin", "Sales", "[Total Sales] * 0.9")
    ]


def test_pbip_thin_report_by_connection(tmp_path):
    # §5.4.1: definition.pbir byConnection → thin report, no local model
    project = write_pbip(
        tmp_path,
        pages=_standard_pages(),
        by_connection={
            "connectionString": (
                "Data Source=powerbi://api.powerbi.com/v1.0/myorg/Shared;" "Initial Catalog=SharedModel;"
            ),
            "pbiModelDatabaseName": "99999999-8888-7777-6666-555555555555",
        },
    )
    result = read_pbip(project)
    assert result.is_thin
    assert result.model is None
    assert result.connection.dataset_id == "99999999-8888-7777-6666-555555555555"
    assert result.report is not None  # report layer still parsed


def test_tmdl_model_inventory(tmp_path):
    project = write_pbip(tmp_path, pages=_standard_pages())
    result = read_pbip(project)
    model = result.model
    assert model is not None
    assert model.name == "Demo"
    assert model.culture == "en-US"

    sales = model.table("Sales")
    assert sales is not None
    assert [c.name for c in sales.columns] == ["Region", "Qty", "Margin"]

    qty = sales.columns[1]
    assert qty.is_hidden and qty.data_type == "int64" and qty.sort_by_column == "Region"

    margin = sales.columns[2]
    assert margin.is_calculated and margin.expression == "[Total Sales] * 0.2"

    total = sales.measures[0]
    assert (total.name, total.expression, total.format_string, total.display_folder) == (
        "Total Sales",
        "SUM(Sales[Qty])",
        "#,0",
        "KPIs",
    )
    multi = sales.measures[1]
    assert multi.name == "Multi Line"
    assert multi.expression == "VAR x = 1\nRETURN x"
    assert multi.format_string == "0.0%"

    assert sales.hierarchies[0].name == "Geo Hierarchy"
    assert sales.hierarchies[0].levels[0].column == "Region"

    partition = sales.partitions[0]
    assert partition.source_type == "m" and partition.mode == "import"
    assert 'Csv.Document(File.Contents("sales.csv"))' in partition.expression


def test_tmdl_relationships(tmp_path):
    from .pbil_fixtures import RELATIONSHIPS_TMDL, SALES_TMDL

    project = write_pbip(
        tmp_path,
        pages={},
        tmdl_files={"tables/Sales.tmdl": SALES_TMDL, "relationships.tmdl": RELATIONSHIPS_TMDL},
    )
    model = read_pbip(project).model
    assert len(model.relationships) == 2
    first, second = model.relationships
    assert (first.from_table, first.from_column, first.to_table, first.to_column) == (
        "Sales",
        "Region",
        "Geo",
        "Region",
    )
    assert first.is_active
    assert not second.is_active
    assert second.to_cardinality == "many"
    assert (second.from_table, second.from_column) == ("Order Lines", "OrderId")


def test_detect_format_variants(tmp_path):
    project = write_pbip(tmp_path, pages={})
    assert detect_format(project) == "pbip"
    assert detect_format(project / "Demo.pbip") == "pbip"
    assert detect_format(project / "Demo.Report") == "pbip"
    # all three entry shapes resolve to the same content
    for entry in (project, project / "Demo.pbip", project / "Demo.Report"):
        result = read_any(entry)
        assert result.source_format == "pbip"
        assert result.model is not None


def test_pbir_hierarchy_level_and_hidden_visual(tmp_path):
    hierarchy_field = {
        "HierarchyLevel": {
            "Expression": {"Hierarchy": {"Expression": entity_ref("Sales"), "Hierarchy": "Geo Hierarchy"}},
            "Level": "Region",
        }
    }
    pages = {
        "p": {
            "page": {"name": "p"},
            "visuals": {
                "v": pbir_visual(projections={"Rows": [hierarchy_field]}, hidden=True),
            },
        }
    }
    result = read_pbip(write_pbip(tmp_path, pages=pages))
    visual = result.report.pages[0].visuals[0]
    assert visual.is_hidden
    assert ("Sales", "Geo Hierarchy.Region", RefKind.HIERARCHY_LEVEL) in visual.fields()
