"""Synthetic PBIX / PBIP fixture builders for the pbi_lineage reader tests.

Each helper builds the *minimum* structure a real file would carry for the
edge case under test (build spec §5.3 / §13 fixture corpus).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Field-expression shapes shared by legacy layout and PBIR
# ---------------------------------------------------------------------------


def entity_ref(table: str) -> dict:
    return {"SourceRef": {"Entity": table}}


def alias_ref(alias: str) -> dict:
    return {"SourceRef": {"Source": alias}}


def column_expr(source: dict, prop: str) -> dict:
    return {"Column": {"Expression": source, "Property": prop}}


def measure_expr(source: dict, prop: str) -> dict:
    return {"Measure": {"Expression": source, "Property": prop}}


# ---------------------------------------------------------------------------
# Legacy layout builders
# ---------------------------------------------------------------------------


def visual_container(
    *,
    name: str = "vc1",
    visual_type: str = "columnChart",
    prototype_query: dict | None = None,
    objects: dict | None = None,
    vc_objects: dict | None = None,
    filters: list | None = None,
    display_hidden: bool = False,
    sync_group: str | None = None,
    extra_single_visual: dict | None = None,
    x: float = 0,
    y: float = 0,
) -> dict:
    single_visual: dict = {"visualType": visual_type}
    if prototype_query is not None:
        single_visual["prototypeQuery"] = prototype_query
    if objects is not None:
        single_visual["objects"] = objects
    if vc_objects is not None:
        single_visual["vcObjects"] = vc_objects
    if display_hidden:
        single_visual["display"] = {"mode": "hidden"}
    if sync_group is not None:
        single_visual["syncGroup"] = {"groupName": sync_group, "fieldChanges": True}
    if extra_single_visual:
        single_visual.update(extra_single_visual)
    container = {
        "x": x,
        "y": y,
        "z": 0,
        "width": 400,
        "height": 300,
        "config": json.dumps({"name": name, "singleVisual": single_visual}),
    }
    if filters is not None:
        container["filters"] = json.dumps(filters)
    return container


def section(
    *,
    name: str = "ReportSection1",
    display_name: str = "Page 1",
    visual_containers: list | None = None,
    filters: list | None = None,
    hidden: bool = False,
    ordinal: int = 0,
) -> dict:
    result = {
        "name": name,
        "displayName": display_name,
        "ordinal": ordinal,
        "visualContainers": visual_containers or [],
        "config": json.dumps({"visibility": 1} if hidden else {}),
    }
    if filters is not None:
        result["filters"] = json.dumps(filters)
    return result


def layout(
    *,
    sections: list,
    report_filters: list | None = None,
    config: dict | None = None,
) -> dict:
    result: dict = {"id": 0, "sections": sections, "resourcePackages": []}
    if report_filters is not None:
        result["filters"] = json.dumps(report_filters)
    if config is not None:
        result["config"] = json.dumps(config)
    return result


def categorical_filter(name: str, expression: dict) -> dict:
    return {"name": name, "expression": expression, "type": "Categorical", "howCreated": 1}


# ---------------------------------------------------------------------------
# PBIX packaging
# ---------------------------------------------------------------------------

THIN_CONNECTIONS = {
    "Version": 3,
    "Connections": [
        {
            "Name": "EntityDataSource",
            "ConnectionString": (
                "Data Source=pbiazure://api.powerbi.com;"
                "Initial Catalog=11111111-2222-3333-4444-555555555555;"
                "Integrated Security=ClaimsToken"
            ),
            "ConnectionType": "pbiServiceLive",
        }
    ],
    "RemoteArtifacts": [{"DatasetId": "11111111-2222-3333-4444-555555555555", "ReportId": "r-1"}],
}


def make_datamashup(m_code: str, junk_prefix: bytes = b"\x00\x00\x00\x08JUNK") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as mashup:
        mashup.writestr("Config/Package.xml", "<Package/>")
        mashup.writestr("Formulas/Section1.m", m_code)
    return junk_prefix + buffer.getvalue()


def write_pbix(
    path: Path,
    layout_doc: dict,
    *,
    encoding: str = "utf-16-le",
    bom: bool = False,
    include_datamodel: bool = True,
    connections: dict | None = None,
    datamashup: bytes | None = None,
) -> Path:
    raw = json.dumps(layout_doc).encode(encoding)
    if bom and encoding == "utf-16-le":
        raw = b"\xff\xfe" + raw
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("Version", "1.28")
        package.writestr("Report/Layout", raw)
        if include_datamodel:
            package.writestr("DataModel", b"ABF-binary-not-parsed")
        if connections is not None:
            package.writestr("Connections", json.dumps(connections))
        if datamashup is not None:
            package.writestr("DataMashup", datamashup)
    return path


# ---------------------------------------------------------------------------
# PBIP / PBIR project builders
# ---------------------------------------------------------------------------

SALES_TMDL = """table Sales
\tcolumn Region
\t\tdataType: string
\t\tsummarizeBy: none
\t\tsourceColumn: Region

\tcolumn Qty
\t\tdataType: int64
\t\tisHidden
\t\tsortByColumn: Region
\t\tsourceColumn: Qty

\tcolumn Margin = [Total Sales] * 0.2
\t\tdataType: double

\tmeasure 'Total Sales' = SUM(Sales[Qty])
\t\tformatString: #,0
\t\tdisplayFolder: KPIs

\tmeasure 'Multi Line' =
\t\t\tVAR x = 1
\t\t\tRETURN x
\t\tformatString: 0.0%

\thierarchy 'Geo Hierarchy'
\t\tlevel Region
\t\t\tcolumn: Region

\tpartition 'Sales-partition' = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = Csv.Document(File.Contents("sales.csv"))
\t\t\tin
\t\t\t\tSource
"""

RELATIONSHIPS_TMDL = """relationship Relationship1
\tfromColumn: Sales.Region
\ttoColumn: Geo.Region

relationship Relationship2
\tisActive: false
\ttoCardinality: many
\tfromColumn: 'Order Lines'.OrderId
\ttoColumn: Orders.OrderId
"""


def pbir_visual(
    *,
    name: str = "v1",
    visual_type: str = "columnChart",
    projections: dict[str, list[dict]] | None = None,
    sort_fields: list[dict] | None = None,
    objects: dict | None = None,
    filters: list[dict] | None = None,
    hidden: bool = False,
) -> dict:
    visual: dict = {"visualType": visual_type}
    if projections:
        visual["query"] = {
            "queryState": {
                bucket: {"projections": [{"field": f, "queryRef": f"q{i}"} for i, f in enumerate(fields)]}
                for bucket, fields in projections.items()
            }
        }
    if sort_fields is not None:
        visual.setdefault("query", {})["sortDefinition"] = {
            "sort": [{"field": f, "direction": "Descending"} for f in sort_fields],
            "isDefaultSort": True,
        }
    if objects is not None:
        visual["objects"] = objects
    doc: dict = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.0.0/schema.json",
        "name": name,
        "position": {"x": 10, "y": 20, "z": 0, "width": 400, "height": 300},
        "visual": visual,
    }
    if filters is not None:
        doc["filterConfig"] = {"filters": [{"field": f, "type": "Categorical"} for f in filters]}
    if hidden:
        doc["isHidden"] = True
    return doc


def write_pbip(
    root: Path,
    *,
    name: str = "Demo",
    pages: dict[str, dict] | None = None,
    report_extensions: dict | None = None,
    tmdl_files: dict[str, str] | None = None,
    by_connection: dict | None = None,
) -> Path:
    """Build a PBIR-format PBIP project.

    `pages` maps page folder name → {"page": page_json, "visuals": {vname: visual_json}}.
    `by_connection` (thin report) replaces the local SemanticModel reference.
    """
    project = root / name
    report_dir = project / f"{name}.Report"
    definition = report_dir / "definition"
    (definition / "pages").mkdir(parents=True)

    (project / f"{name}.pbip").write_text(
        json.dumps({"version": "1.0", "artifacts": [{"report": {"path": f"{name}.Report"}}]}),
        encoding="utf-8",
    )

    if by_connection is not None:
        dataset_reference = {"byConnection": by_connection}
    else:
        dataset_reference = {"byPath": {"path": f"../{name}.SemanticModel"}}
    (report_dir / "definition.pbir").write_text(
        json.dumps({"version": "1.0", "datasetReference": dataset_reference}), encoding="utf-8"
    )

    (definition / "report.json").write_text(json.dumps({"themeCollection": {}}), encoding="utf-8")
    if report_extensions is not None:
        (definition / "reportExtensions.json").write_text(json.dumps(report_extensions), encoding="utf-8")

    pages = pages or {}
    (definition / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": list(pages), "activePageName": next(iter(pages), None)}),
        encoding="utf-8",
    )
    for page_name, content in pages.items():
        page_dir = definition / "pages" / page_name
        page_dir.mkdir(parents=True)
        (page_dir / "page.json").write_text(
            json.dumps(content.get("page", {"name": page_name})), encoding="utf-8"
        )
        for visual_name, visual_json in content.get("visuals", {}).items():
            visual_dir = page_dir / "visuals" / visual_name
            visual_dir.mkdir(parents=True)
            (visual_dir / "visual.json").write_text(json.dumps(visual_json), encoding="utf-8")

    if by_connection is None:
        model_definition = project / f"{name}.SemanticModel" / "definition"
        tables_dir = model_definition / "tables"
        tables_dir.mkdir(parents=True)
        (model_definition / "database.tmdl").write_text(
            f"database {name}\n\tcompatibilityLevel: 1604\n", encoding="utf-8"
        )
        (model_definition / "model.tmdl").write_text(
            "model Model\n\tculture: en-US\n\nref table Sales\n", encoding="utf-8"
        )
        for file_name, text in (tmdl_files or {"tables/Sales.tmdl": SALES_TMDL}).items():
            target = model_definition / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return project
