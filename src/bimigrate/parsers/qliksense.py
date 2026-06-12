"""Qlik Sense parser (.qvf binary best-effort, Engine-API JSON app exports).

The recommended high-fidelity path is an Engine JSON export (one JSON
document per app, as produced by the Engine API / qlik-cli `app export
--json` or an unbuild folder). This parser consumes:

* `.json` app exports: sheets, objects, master measures/dimensions,
  variables, load script, stories — full fidelity.
* `.qvf` binary containers: heuristic extraction of the load script and
  embedded JSON object fragments, with explicit ParseIssues for the gaps.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bimigrate.models.core import SourceTool
from bimigrate.models.inventory import (
    ArtifactInventory,
    Calculation,
    ParseIssue,
    ScriptBlock,
    SecurityRule,
    Visual,
    Worksheet,
)
from bimigrate.parsers.base import BaseParser, ParserError, register
from bimigrate.parsers.qlik_script import parse_load_script

_VIS_MAP = {
    "barchart": "bar",
    "linechart": "line",
    "piechart": "pie",
    "combochart": "combo",
    "scatterplot": "scatter",
    "table": "table",
    "pivot-table": "pivot_table",
    "treemap": "tree_map",
    "kpi": "kpi",
    "gauge": "gauge",
    "map": "map",
    "histogram": "histogram",
    "boxplot": "box_plot",
    "waterfallchart": "waterfall",
    "distributionplot": "custom",
    "bulletchart": "bullet",
    "mekkochart": "custom",
    "text-image": "text",
    "filterpane": "filter",
    "listbox": "filter",
    "sn-grid-chart": "custom",
}


@register
class QlikSenseParser(BaseParser):
    extensions = (".qvf", ".json")

    def parse(self, path: Path) -> ArtifactInventory:
        inv = ArtifactInventory(
            source_tool=SourceTool.QLIKSENSE,
            artifact_name=path.stem,
            artifact_path=str(path),
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ParserError(f"cannot read {path.name}: {exc}") from exc

        if path.suffix.lower() == ".json":
            try:
                doc = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ParserError(f"{path.name}: invalid JSON app export: {exc}") from exc
            self._parse_app_json(doc, inv)
        else:
            self._parse_qvf_binary(data, inv)

        self._collect_feature_flags(inv)
        inv.fingerprint = self.fingerprint(inv)
        return inv

    # -- JSON app export ----------------------------------------------------

    def _parse_app_json(self, doc: dict[str, Any], inv: ArtifactInventory) -> None:
        props = doc.get("properties") or doc.get("qProperty") or {}
        inv.artifact_name = props.get("qTitle") or doc.get("title") or inv.artifact_name

        script = doc.get("loadScript") or doc.get("script") or ""
        if script:
            self._ingest_script(script, inv)

        for obj in self._iter_objects(doc):
            qtype = (obj.get("qInfo", {}).get("qType") or obj.get("qType") or "").lower()
            qid = obj.get("qInfo", {}).get("qId") or obj.get("qId") or ""
            meta = obj.get("qMetaDef", {}) or obj.get("qMeta", {})
            title = meta.get("title") or obj.get("title") or qid

            if qtype == "sheet":
                inv.worksheets.append(Worksheet(name=title))
            elif qtype == "story":
                inv.stories.append(title)
            elif qtype == "measure":
                expr = obj.get("qMeasure", {}).get("qDef", "")
                inv.calculations.append(
                    Calculation(
                        name=title,
                        expression=expr,
                        kind="measure",
                        caption=obj.get("qMeasure", {}).get("qLabel"),
                    )
                )
            elif qtype == "dimension":
                defs = obj.get("qDim", {}).get("qFieldDefs", [])
                inv.platform_extras.setdefault("master_dimensions", []).append(
                    {"name": title, "fields": defs}
                )
            elif qtype in _VIS_MAP or qtype:
                self._add_visual(qtype, title, obj, inv)

        for var in doc.get("variables", []) or []:
            inv.calculations.append(
                Calculation(
                    name=var.get("qName", "variable"), expression=var.get("qDefinition", ""), kind="variable"
                )
            )

    def _iter_objects(self, doc: dict[str, Any]):
        for key in ("objects", "qChildren", "sheets", "appObjects"):
            for obj in doc.get(key, []) or []:
                yield obj
                yield from obj.get("qChildren", []) or []
                for cell in obj.get("cells", []) or []:
                    yield cell

    def _add_visual(self, qtype: str, title: str, obj: dict[str, Any], inv: ArtifactInventory) -> None:
        norm = _VIS_MAP.get(qtype, "custom")
        fields: list[str] = []
        hypercube = obj.get("qHyperCubeDef", {})
        for dim in hypercube.get("qDimensions", []) or []:
            fields.extend(dim.get("qDef", {}).get("qFieldDefs", []) or [])
        for meas in hypercube.get("qMeasures", []) or []:
            expr = meas.get("qDef", {}).get("qDef", "")
            if expr:
                inv.calculations.append(
                    Calculation(
                        name=meas.get("qDef", {}).get("qLabel") or f"{title}-measure",
                        expression=expr,
                        kind="set_analysis" if "{" in expr else "measure",
                    )
                )
        visual = Visual(
            name=title, visual_type=norm, raw_type=qtype, fields=fields, is_custom=norm == "custom"
        )
        if inv.worksheets:
            inv.worksheets[-1].visuals.append(visual)
        else:
            inv.worksheets.append(Worksheet(name="Sheet 1", visuals=[visual]))

    # -- QVF binary ------------------------------------------------------------

    def _parse_qvf_binary(self, data: bytes, inv: ArtifactInventory) -> None:
        text = data.decode("utf-8", errors="replace")

        script_match = re.search(
            r"((?:///\$tab|SET\s+ThousandSep)[\s\S]{0,200000}?)(?:�{4,}|\x00{4,}|$)", text
        )
        if script_match and "LOAD" in script_match.group(1).upper():
            self._ingest_script(script_match.group(1), inv)
        else:
            inv.issues.append(
                ParseIssue(
                    severity="warning",
                    location="load-script",
                    message="load script not recoverable from .qvf binary; export the app "
                    "as JSON via the Engine API / qlik-cli for full fidelity",
                )
            )

        for qtype, norm in _VIS_MAP.items():
            count = len(re.findall(rf'"qType"\s*:\s*"{re.escape(qtype)}"', text))
            if count:
                ws = inv.worksheets[0] if inv.worksheets else Worksheet(name="Main")
                if not inv.worksheets:
                    inv.worksheets.append(ws)
                ws.visuals.extend(
                    Visual(name=f"{qtype}-{i}", visual_type=norm, raw_type=qtype) for i in range(count)
                )

        inv.issues.append(
            ParseIssue(
                severity="info",
                location=inv.artifact_path,
                message=".qvf parsed heuristically; master items, stories and object "
                "layout require the Engine JSON export path",
            )
        )

    # -- shared ------------------------------------------------------------------

    def _ingest_script(self, script: str, inv: ArtifactInventory) -> None:
        inv.scripts.append(
            ScriptBlock(
                name="load-script", language="qlik-load-script", code=script, purpose="Qlik Sense load script"
            )
        )
        parsed = parse_load_script(script)
        for name, value in parsed.variables.items():
            inv.calculations.append(Calculation(name=name, expression=value, kind="variable"))
        if parsed.has_section_access:
            inv.security.append(
                SecurityRule(kind="section_access", definition="SECTION ACCESS block in load script")
            )
        inv.platform_extras["qvd_reads"] = parsed.qvd_reads
        inv.platform_extras["qvd_writes"] = parsed.qvd_writes

    def _collect_feature_flags(self, inv: ArtifactInventory) -> None:
        flags = inv.feature_flags
        flags["sheets"] = len(inv.worksheets)
        flags["stories"] = len(inv.stories)
        flags["master_measures"] = sum(1 for c in inv.calculations if c.kind == "measure")
        flags["master_dimensions"] = len(inv.platform_extras.get("master_dimensions", []))
        flags["variables"] = sum(1 for c in inv.calculations if c.kind == "variable")
        flags["set_analysis"] = sum(1 for c in inv.calculations if c.kind == "set_analysis")
        flags["custom_extensions"] = sum(1 for ws in inv.worksheets for v in ws.visuals if v.is_custom)
        flags["section_access"] = sum(1 for s in inv.security if s.kind == "section_access")
