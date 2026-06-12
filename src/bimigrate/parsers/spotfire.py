"""Spotfire analysis file parser (.dxp).

A DXP is a zip archive mixing XML parts with proprietary binary streams.
This parser extracts everything that is reliably present in the XML/text
parts: pages, visualization types, data tables and columns, information
links, data functions (TERR/R/Python), IronPython scripts, document
properties, calculated columns and transformations. Binary-only parts are
recorded as ParseIssues instead of being silently dropped.
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

# Spotfire visualization class names -> normalized type
_VIS_MAP = {
    "ScatterPlot": "scatter",
    "BarChart": "bar",
    "LineChart": "line",
    "CombinationChart": "combo",
    "PieChart": "pie",
    "HeatMap": "heat_map",
    "CrossTablePlot": "pivot_table",
    "Table": "table",
    "TablePlot": "table",
    "BoxPlot": "box_plot",
    "MapChart": "map",
    "KpiChart": "kpi",
    "TreemapPlot": "tree_map",
    "Treemap": "tree_map",
    "WaterfallChart": "waterfall",
    "ParallelCoordinatePlot": "custom",
    "SummaryTable": "table",
    "GraphicalTable": "table",
    "TextArea": "text",
    "HtmlTextArea": "text",
    "NetworkChart": "custom",
}

_SCRIPT_LANG_MAP = {
    "ironpython": "ironpython",
    "python": "python",
    "terr": "terr",
    "r": "r",
    "open source r": "r",
}


@register
class SpotfireParser(BaseParser):
    extensions = (".dxp",)

    def parse(self, path: Path) -> ArtifactInventory:
        inv = ArtifactInventory(
            source_tool=SourceTool.SPOTFIRE,
            artifact_name=path.stem,
            artifact_path=str(path),
        )
        try:
            archive = zipfile.ZipFile(io.BytesIO(path.read_bytes()))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ParserError(f"cannot open DXP archive {path.name}: {exc}") from exc

        binary_parts = 0
        with archive:
            for entry in archive.namelist():
                try:
                    data = archive.read(entry)
                except Exception:
                    inv.issues.append(
                        ParseIssue(severity="warning", location=entry, message="unreadable zip entry")
                    )
                    continue
                if self._looks_like_xml(data):
                    self._scan_xml_part(entry, data, inv)
                else:
                    binary_parts += 1
                    self._scan_binary_part(entry, data, inv)

        if binary_parts:
            inv.issues.append(
                ParseIssue(
                    severity="info",
                    location=path.name,
                    message=f"{binary_parts} proprietary binary parts scanned heuristically; "
                    "layout coordinates and some visual config may be incomplete",
                )
            )
        self._collect_feature_flags(inv)
        inv.fingerprint = self.fingerprint(inv)
        return inv

    @staticmethod
    def _looks_like_xml(data: bytes) -> bool:
        head = data[:200].lstrip(b"\xef\xbb\xbf \r\n\t")
        return head.startswith(b"<")

    # -- XML parts -------------------------------------------------------

    def _scan_xml_part(self, entry: str, data: bytes, inv: ArtifactInventory) -> None:
        root = etree.fromstring(data, parser=etree.XMLParser(recover=True, huge_tree=True))
        if root is None:
            return
        for el in root.iter():
            if el.tag is etree.Comment:
                continue
            tag = etree.QName(el).localname

            if tag in ("Page", "PageNode") and (el.get("Title") or el.get("title")):
                name = el.get("Title") or el.get("title") or "page"
                if not any(ws.name == name for ws in inv.worksheets):
                    inv.worksheets.append(Worksheet(name=name))

            elif tag in ("Visualization", "Visual", "Plot"):
                self._add_visual(el, inv)

            elif tag in ("DataTable", "Table") and el.get("Name"):
                name = el.get("Name") or "table"
                cols = [
                    c.get("Name")
                    for c in el.iter()
                    if c.tag is not etree.Comment and etree.QName(c).localname == "Column" and c.get("Name")
                ]
                if not any(t.name == name for t in inv.tables):
                    inv.tables.append(DataTable(name=name, columns=[c for c in cols if c]))

            elif tag == "InformationLink":
                inv.connections.append(
                    DataConnection(
                        name=el.get("Name") or el.get("Path") or "information-link",
                        connection_type="information-link",
                    )
                )

            elif tag in ("DataFunction", "DataFunctionDefinition"):
                lang_raw = (el.get("FunctionType") or el.get("Language") or "terr").lower()
                lang = _SCRIPT_LANG_MAP.get(lang_raw, "terr")
                code = (el.findtext(".//{*}Script") or el.get("Script") or "").strip()
                inv.scripts.append(
                    ScriptBlock(
                        name=el.get("Name") or "data-function",
                        language=lang,
                        code=code,
                        purpose="Spotfire data function",
                    )
                )

            elif tag in ("Script", "ScriptDefinition") and (el.get("Name") or el.findtext("{*}Name")):
                code = el.findtext(".//{*}ScriptCode") or el.findtext(".//{*}Code") or el.text or ""
                lang_raw = (el.get("Language") or "IronPython").lower()
                inv.scripts.append(
                    ScriptBlock(
                        name=el.get("Name") or el.findtext("{*}Name") or "script",
                        language=_SCRIPT_LANG_MAP.get(lang_raw, "ironpython"),
                        code=code.strip(),
                        purpose="Spotfire document script (action control)",
                    )
                )

            elif tag in ("CalculatedColumn",) and el.get("Name"):
                inv.calculations.append(
                    Calculation(
                        name=el.get("Name") or "calc",
                        expression=(el.get("Expression") or el.findtext("{*}Expression") or "").strip(),
                        kind="calculated_field",
                    )
                )

            elif tag in ("DocumentProperty", "Property") and el.get("Name"):
                inv.parameters.append(el.get("Name") or "")

            elif tag in ("Transformation",):
                kind = el.get("Type") or el.get("type") or "transformation"
                inv.feature_flags[f"transformation_{kind.lower()}"] = (
                    inv.feature_flags.get(f"transformation_{kind.lower()}", 0) + 1
                )

            elif tag in ("LibraryPermission", "DataRestriction"):
                inv.security.append(
                    SecurityRule(
                        kind="library_permission" if tag == "LibraryPermission" else "data_restriction",
                        subject=el.get("Principal") or el.get("User") or "",
                        definition=etree.tostring(el, encoding="unicode")[:500],
                    )
                )

    def _add_visual(self, el: etree._Element, inv: ArtifactInventory) -> None:
        raw = el.get("Type") or el.get("TypeId") or el.get("xsi_type") or ""
        raw_short = raw.split(".")[-1].replace("Spotfire", "").strip() or "Visualization"
        norm = next((v for k, v in _VIS_MAP.items() if k.lower() in raw_short.lower()), "custom")
        title = el.get("Title") or el.get("Name") or raw_short
        visual = Visual(
            name=title,
            visual_type=norm,
            raw_type=raw_short,
            is_custom=norm == "custom" or "mod" in raw_short.lower(),
        )
        if inv.worksheets:
            inv.worksheets[-1].visuals.append(visual)
        else:
            inv.worksheets.append(Worksheet(name="Page 1", visuals=[visual]))

    # -- binary parts (heuristic string scan) -----------------------------

    _EXPR_RE = re.compile(
        rb"((?:Sum|Avg|Min|Max|Count|UniqueCount|Median|StdDev|Rank|First|Last)"
        rb"\(\[[^\]\x00]{1,120}\][^\x00\r\n]{0,200}?OVER\s*\([^\x00\r\n]{1,200}?\))",
        re.IGNORECASE,
    )

    def _scan_binary_part(self, entry: str, data: bytes, inv: ArtifactInventory) -> None:
        # OVER expressions embedded in proprietary streams are still worth surfacing
        for match in self._EXPR_RE.finditer(data):
            try:
                expr = match.group(1).decode("utf-8", errors="ignore")
            except Exception:
                continue
            if not any(c.expression == expr for c in inv.calculations):
                inv.calculations.append(
                    Calculation(name=f"over-expr-{len(inv.calculations)}", expression=expr, kind="lod")
                )
        if b"IronPython" in data and not any(s.language == "ironpython" for s in inv.scripts):
            inv.feature_flags["ironpython_detected"] = 1

    def _collect_feature_flags(self, inv: ArtifactInventory) -> None:
        flags = inv.feature_flags
        flags["pages"] = len(inv.worksheets)
        flags["data_tables"] = len(inv.tables)
        flags["information_links"] = sum(
            1 for c in inv.connections if c.connection_type == "information-link"
        )
        flags["data_functions"] = sum(1 for s in inv.scripts if s.language in ("terr", "r", "python"))
        flags["ironpython_scripts"] = sum(1 for s in inv.scripts if s.language == "ironpython")
        flags["document_properties"] = len(inv.parameters)
        flags["calculated_columns"] = len(inv.calculations)
        flags["over_expressions"] = sum(
            1 for c in inv.calculations if re.search(r"\bOVER\s*\(", c.expression, re.IGNORECASE)
        )
        flags["custom_mods"] = sum(1 for ws in inv.worksheets for v in ws.visuals if v.is_custom)
