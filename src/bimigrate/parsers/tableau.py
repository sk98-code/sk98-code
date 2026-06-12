"""Tableau workbook parser (.twb XML, .twbx packaged zip).

Covers the Section 4.1 feature surface that is representable in the
workbook XML: dashboards, stories, worksheets, mark types, actions,
calculated fields (incl. LOD and table calcs), data sources (joins,
relationships, custom SQL, extracts, blends), parameters, and the
security features embedded in workbooks (user filters, data source
filters). Server-side features (schedules, subscriptions, permissions)
require the REST API and are flagged via feature_flags when referenced.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from lxml import etree

from bimigrate.models.core import SourceTool
from bimigrate.models.inventory import (
    ArtifactInventory,
    Calculation,
    DataConnection,
    DataTable,
    ParseIssue,
    ScriptBlock,
    SecurityRule,
    Visual,
    Worksheet,
)
from bimigrate.parsers.base import BaseParser, ParserError, register

_LOD_RE = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.IGNORECASE)
_TABLE_CALC_RE = re.compile(
    r"\b(WINDOW_\w+|RUNNING_\w+|INDEX|FIRST|LAST|RANK|RANK_DENSE|RANK_MODIFIED|"
    r"RANK_PERCENTILE|RANK_UNIQUE|LOOKUP|PREVIOUS_VALUE|TOTAL|SIZE)\s*\(",
    re.IGNORECASE,
)

# Tableau mark class -> normalized visual type
_MARK_MAP = {
    "Bar": "bar",
    "Line": "line",
    "Area": "area",
    "Square": "heat_map",
    "Circle": "scatter",
    "Shape": "scatter",
    "Text": "text_table",
    "Map": "map",
    "Pie": "pie",
    "GanttBar": "gantt",
    "Polygon": "map",
    "Multipolygon": "map",
    "Automatic": "auto",
}


@register
class TableauParser(BaseParser):
    extensions = (".twb", ".twbx")

    def parse(self, path: Path) -> ArtifactInventory:
        try:
            xml_bytes = self._read_twb_bytes(path)
            root = etree.fromstring(xml_bytes, parser=etree.XMLParser(recover=True, huge_tree=True))
        except (zipfile.BadZipFile, etree.XMLSyntaxError, OSError) as exc:
            raise ParserError(f"cannot read Tableau workbook {path.name}: {exc}") from exc
        if root is None:
            raise ParserError(f"{path.name}: empty or unparseable workbook XML")

        inv = ArtifactInventory(
            source_tool=SourceTool.TABLEAU,
            artifact_name=path.stem,
            artifact_path=str(path),
        )
        self._parse_datasources(root, inv)
        self._parse_worksheets(root, inv)
        self._parse_dashboards_and_stories(root, inv)
        self._parse_actions(root, inv)
        self._collect_feature_flags(root, inv)
        inv.fingerprint = self.fingerprint(inv)
        return inv

    # -- file handling -------------------------------------------------

    @staticmethod
    def _read_twb_bytes(path: Path) -> bytes:
        if path.suffix.lower() == ".twb":
            return path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
            twb_names = [n for n in zf.namelist() if n.lower().endswith(".twb")]
            if not twb_names:
                raise ParserError(f"{path.name}: no .twb inside .twbx archive")
            return zf.read(twb_names[0])

    # -- datasources ---------------------------------------------------

    def _parse_datasources(self, root: etree._Element, inv: ArtifactInventory) -> None:
        for ds in root.iterfind(".//datasources/datasource"):
            ds_name = ds.get("caption") or ds.get("name") or "datasource"
            if ds.get("name") == "Parameters":
                for col in ds.iterfind(".//column"):
                    inv.parameters.append(col.get("caption") or col.get("name") or "parameter")
                continue

            for conn in ds.iterfind(".//connection"):
                klass = conn.get("class") or "unknown"
                if klass == "federated":
                    continue  # wrapper around named connections
                is_extract = klass == "hyper" or conn.get("dbname", "").endswith(".hyper")
                inv.connections.append(
                    DataConnection(
                        name=ds_name,
                        connection_type=klass,
                        server=conn.get("server"),
                        database=conn.get("dbname"),
                        is_extract=is_extract,
                    )
                )

            for rel in ds.iterfind(".//relation"):
                rel_type = rel.get("type") or ""
                if rel_type == "text" and rel.text and rel.text.strip():
                    inv.scripts.append(
                        ScriptBlock(
                            name=f"{ds_name}/{rel.get('name') or 'custom-sql'}",
                            language="custom-sql",
                            code=rel.text.strip(),
                            purpose="Tableau Custom SQL relation",
                        )
                    )
                    inv.feature_flags["custom_sql"] = inv.feature_flags.get("custom_sql", 0) + 1
                elif rel_type == "join":
                    inv.feature_flags["joins"] = inv.feature_flags.get("joins", 0) + 1
                elif rel_type == "table":
                    inv.tables.append(
                        DataTable(
                            name=rel.get("name") or rel.get("table") or "table",
                            source_connection=ds_name,
                        )
                    )

            # logical-layer relationships (Tableau 2020.2+)
            for rel in ds.iterfind(".//object-graph//relationship"):
                inv.relationships.append(
                    {"datasource": ds_name, "expression": (rel.findtext("expression") or "").strip()}
                )

            self._parse_columns(ds, ds_name, inv)

            for dsf in ds.iterfind(".//filter"):
                # datasource-level filters double as RLS when fed by USERNAME()/ISMEMBEROF()
                expr = etree.tostring(dsf, encoding="unicode")
                if re.search(r"USERNAME\s*\(|ISMEMBEROF\s*\(|FULLNAME\s*\(", expr, re.IGNORECASE):
                    inv.security.append(
                        SecurityRule(
                            kind="datasource_filter",
                            subject=dsf.get("column") or "",
                            definition=expr[:500],
                        )
                    )

    def _parse_columns(self, ds: etree._Element, ds_name: str, inv: ArtifactInventory) -> None:
        table_cols: list[str] = []
        for col in ds.iterfind("column"):
            name = (col.get("caption") or col.get("name") or "").strip("[]")
            calc = col.find("calculation")
            if calc is not None and calc.get("formula"):
                formula = calc.get("formula") or ""
                kind = "calculated_field"
                if _LOD_RE.search(formula):
                    kind = "lod"
                elif _TABLE_CALC_RE.search(formula):
                    kind = "table_calc"
                inv.calculations.append(
                    Calculation(
                        name=name or (col.get("name") or "calc").strip("[]"),
                        expression=formula,
                        kind=kind,
                        datatype=col.get("datatype"),
                        caption=col.get("caption"),
                        dependencies=re.findall(r"\[([^\]]+)\]", formula),
                    )
                )
            elif name:
                table_cols.append(name)
        if table_cols:
            inv.tables.append(DataTable(name=ds_name, columns=table_cols, source_connection=ds_name))

    # -- worksheets / visuals -------------------------------------------

    def _parse_worksheets(self, root: etree._Element, inv: ArtifactInventory) -> None:
        for ws in root.iterfind(".//worksheets/worksheet"):
            ws_name = ws.get("name") or "worksheet"
            sheet = Worksheet(name=ws_name)
            panes = ws.findall(".//pane")
            for pane in panes:
                mark = pane.find("mark")
                raw = mark.get("class") if mark is not None else "Automatic"
                fields = [
                    f.strip("[]")
                    for enc in pane.iterfind(".//encodings/*")
                    if (f := enc.get("column")) is not None
                ]
                sheet.visuals.append(
                    Visual(
                        name=ws_name,
                        visual_type=_MARK_MAP.get(raw or "Automatic", "custom"),
                        raw_type=raw or "Automatic",
                        worksheet=ws_name,
                        fields=fields,
                        filters=[(flt.get("column") or "").strip("[]") for flt in ws.iterfind(".//filter")],
                        is_custom=raw not in _MARK_MAP if raw else False,
                    )
                )
            if not panes:
                inv.issues.append(
                    ParseIssue(severity="info", location=ws_name, message="worksheet has no panes")
                )
            inv.worksheets.append(sheet)

    def _parse_dashboards_and_stories(self, root: etree._Element, inv: ArtifactInventory) -> None:
        for db in root.iterfind(".//dashboards/dashboard"):
            name = db.get("name") or "dashboard"
            if db.get("type") == "storyboard" or db.find(".//story") is not None:
                inv.stories.append(name)
            else:
                inv.dashboards.append(name)

    def _parse_actions(self, root: etree._Element, inv: ArtifactInventory) -> None:
        kind_by_tag = {"filter": "filter_action", "highlight": "highlight_action", "url": "url_action"}
        for action in root.iterfind(".//actions/action"):
            name = action.get("caption") or action.get("name") or "action"
            kind = "action"
            for child in action:
                tag = etree.QName(child).localname if child.tag is not etree.Comment else ""
                if tag in ("link", "activation"):
                    continue
                kind = kind_by_tag.get(tag, f"{tag}_action")
            inv.feature_flags[kind] = inv.feature_flags.get(kind, 0) + 1
            source_sheet = action.find(".//source")
            target = (source_sheet.get("worksheet") if source_sheet is not None else None) or ""
            for ws in inv.worksheets:
                if ws.name == target or not target:
                    ws.actions.append(f"{kind}:{name}")
                    break

    # -- feature flags ---------------------------------------------------

    def _collect_feature_flags(self, root: etree._Element, inv: ArtifactInventory) -> None:
        flags = inv.feature_flags
        flags["worksheets"] = len(inv.worksheets)
        flags["dashboards"] = len(inv.dashboards)
        flags["stories"] = len(inv.stories)
        flags["parameters"] = len(inv.parameters)
        flags["calculated_fields"] = sum(1 for c in inv.calculations if c.kind == "calculated_field")
        flags["lod_expressions"] = sum(1 for c in inv.calculations if c.kind == "lod")
        flags["nested_lod"] = sum(1 for c in inv.calculations if len(_LOD_RE.findall(c.expression)) > 1)
        flags["table_calculations"] = sum(1 for c in inv.calculations if c.kind == "table_calc")
        flags["extracts"] = sum(1 for c in inv.connections if c.is_extract)
        flags["live_connections"] = sum(1 for c in inv.connections if not c.is_extract)
        flags["relationships"] = len(inv.relationships)
        flags["user_filters"] = sum(1 for s in inv.security if s.kind == "datasource_filter")
        flags["dual_axis"] = len(root.findall(".//pane[@id='2']"))
        if root.find(".//worksheet//groupfilter") is not None:
            flags["group_filters"] = 1
