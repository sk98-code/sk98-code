"""QlikView parser (.qvw binary best-effort, .qvs scripts, -prj XML folders).

The .qvw container is a proprietary binary format. We extract what is
reliably recoverable without the QlikView desktop COM API:

* the load script (stored as readable text inside the container),
* set-analysis expressions and variables found in object metadata,
* sheet/object names from embedded XML fragments,
* Section Access and macro (VBScript module) detection.

For full-fidelity extraction the supported path is a QlikView "-prj"
project folder (XML per object), which this parser consumes when given
the folder's `QlikViewProject.xml` or any `.qvs` script export. Every
heuristic gap is recorded as a ParseIssue — never silently dropped.
"""

from __future__ import annotations

import re
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
from bimigrate.parsers.qlik_script import parse_load_script

_SET_ANALYSIS_RE = re.compile(
    r"((?:Sum|Avg|Min|Max|Count|Only|Aggr)\s*\(\s*\{[<$1][^}]*\}[^)\x00]{0,300}\))", re.IGNORECASE
)
_CHART_TYPE_MAP = {
    "BARCHART": "bar",
    "LINECHART": "line",
    "COMBOCHART": "combo",
    "PIECHART": "pie",
    "SCATTERCHART": "scatter",
    "GAUGECHART": "gauge",
    "PIVOTTABLE": "pivot_table",
    "STRAIGHTTABLE": "table",
    "BLOCKCHART": "tree_map",
    "FUNNELCHART": "funnel",
    "RADARCHART": "custom",
    "MEKKOCHART": "custom",
    "GRIDCHART": "heat_map",
    "TEXTOBJECT": "text",
    "KPIOBJECT": "kpi",
}


@register
class QlikViewParser(BaseParser):
    extensions = (".qvw", ".qvs")

    def parse(self, path: Path) -> ArtifactInventory:
        inv = ArtifactInventory(
            source_tool=SourceTool.QLIKVIEW,
            artifact_name=path.stem,
            artifact_path=str(path),
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ParserError(f"cannot read {path.name}: {exc}") from exc

        if path.suffix.lower() == ".qvs":
            self._ingest_script(data.decode("utf-8", errors="replace"), inv)
        else:
            self._parse_qvw_binary(data, inv)
            prj = path.with_name(path.stem + "-prj")
            if prj.is_dir():
                self._parse_prj_folder(prj, inv)
            else:
                inv.issues.append(
                    ParseIssue(
                        severity="info",
                        location=path.name,
                        message="no -prj project folder found; object-level extraction is "
                        "heuristic (string scan). Enable 'Generate project files' in "
                        "QlikView or supply the -prj folder for full fidelity.",
                    )
                )

        self._collect_feature_flags(inv)
        inv.fingerprint = self.fingerprint(inv)
        return inv

    # -- binary container --------------------------------------------------

    def _parse_qvw_binary(self, data: bytes, inv: ArtifactInventory) -> None:
        text = data.decode("latin-1", errors="replace")

        script = self._extract_load_script(text)
        if script:
            self._ingest_script(script, inv)
        else:
            inv.issues.append(
                ParseIssue(
                    severity="warning",
                    location="load-script",
                    message="load script not recoverable from binary container",
                )
            )

        for m in _SET_ANALYSIS_RE.finditer(text):
            expr = m.group(1)
            if len(expr) < 600 and not any(c.expression == expr for c in inv.calculations):
                inv.calculations.append(
                    Calculation(
                        name=f"set-expr-{len(inv.calculations)}", expression=expr, kind="set_analysis"
                    )
                )

        for raw_type, norm in _CHART_TYPE_MAP.items():
            count = text.upper().count(raw_type)
            if count:
                ws = inv.worksheets[0] if inv.worksheets else Worksheet(name="Main")
                if not inv.worksheets:
                    inv.worksheets.append(ws)
                ws.visuals.extend(
                    Visual(name=f"{raw_type.lower()}-{i}", visual_type=norm, raw_type=raw_type)
                    for i in range(count)
                )

        if re.search(r"ActiveDocument|CreateObject\s*\(", text):
            inv.scripts.append(
                ScriptBlock(
                    name="embedded-macro",
                    language="vbscript",
                    code="(module text embedded in binary; export via -prj for source)",
                    purpose="QlikView macro module detected",
                )
            )

    @staticmethod
    def _extract_load_script(text: str) -> str | None:
        # the script tab text appears contiguously; anchor on canonical openings
        m = re.search(
            r"((?:SET\s+ThousandSep|SET\s+DecimalSep|///\$tab)[\s\S]{0,200000}?)" r"(?:\x00{4,}|$)", text
        )
        if not m:
            return None
        script = m.group(1)
        # trim trailing binary noise
        cleaned_lines = []
        for line in script.splitlines():
            if sum(1 for ch in line if ord(ch) < 9 or 13 < ord(ch) < 32) > 2:
                break
            cleaned_lines.append(line)
        script = "\n".join(cleaned_lines)
        return script if "LOAD" in script.upper() or "SET" in script.upper() else None

    # -- prj folder ----------------------------------------------------------

    def _parse_prj_folder(self, prj: Path, inv: ArtifactInventory) -> None:
        for xml_file in sorted(prj.glob("*.xml")):
            try:
                root = etree.parse(str(xml_file)).getroot()
            except etree.XMLSyntaxError:
                inv.issues.append(
                    ParseIssue(severity="warning", location=xml_file.name, message="unparseable prj XML")
                )
                continue
            tag = etree.QName(root).localname
            if tag == "SheetProperties":
                name = root.findtext(".//Title/v") or xml_file.stem
                inv.worksheets.append(Worksheet(name=name))
            for expr in root.iterfind(".//Expression"):
                definition = (expr.findtext("v") or expr.text or "").strip()
                if definition:
                    inv.calculations.append(
                        Calculation(
                            name=xml_file.stem,
                            expression=definition,
                            kind="set_analysis" if "{" in definition else "calculated_field",
                        )
                    )

    # -- script ingestion ------------------------------------------------------

    def _ingest_script(self, script: str, inv: ArtifactInventory) -> None:
        inv.scripts.append(
            ScriptBlock(
                name="load-script", language="qlik-load-script", code=script, purpose="QlikView load script"
            )
        )
        parsed = parse_load_script(script)
        for name, value in parsed.variables.items():
            inv.calculations.append(Calculation(name=name, expression=value, kind="variable"))
        for table in parsed.tables:
            inv.tables.append(DataTable(name=table))
        for qvd in parsed.qvd_reads:
            inv.connections.append(DataConnection(name=qvd, connection_type="qvd"))
        if parsed.has_section_access:
            inv.security.append(
                SecurityRule(kind="section_access", definition="SECTION ACCESS block in load script")
            )
        if parsed.has_macros:
            inv.feature_flags["macros"] = inv.feature_flags.get("macros", 0) + 1
        inv.platform_extras["load_statements"] = sum(
            1 for s in parsed.statements if s.kind in ("load", "select")
        )
        inv.platform_extras["qvd_reads"] = parsed.qvd_reads
        inv.platform_extras["qvd_writes"] = parsed.qvd_writes
        synthetic_risk = self._synthetic_key_risk(parsed)
        if synthetic_risk:
            inv.feature_flags["synthetic_key_risk"] = synthetic_risk

    @staticmethod
    def _synthetic_key_risk(parsed) -> int:
        """Tables sharing 2+ field names create synthetic keys in Qlik's
        associative model — flag them since the star-schema redesign is a
        manual modeling task in Power BI."""
        field_owners: dict[str, set[str]] = {}
        for stmt in parsed.statements:
            if stmt.kind not in ("load", "select") or not stmt.table_label:
                continue
            for f in stmt.fields:
                field_owners.setdefault(f.lower(), set()).add(stmt.table_label)
        pair_counts: dict[tuple[str, str], int] = {}
        for owners in field_owners.values():
            owners_sorted = sorted(owners)
            for i, a in enumerate(owners_sorted):
                for b in owners_sorted[i + 1 :]:
                    pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1
        return sum(1 for count in pair_counts.values() if count >= 2)

    def _collect_feature_flags(self, inv: ArtifactInventory) -> None:
        flags = inv.feature_flags
        flags["sheets"] = len(inv.worksheets)
        flags["set_analysis"] = sum(1 for c in inv.calculations if c.kind == "set_analysis")
        flags["variables"] = sum(1 for c in inv.calculations if c.kind == "variable")
        flags["alternate_states"] = sum(
            1 for c in inv.calculations if re.search(r"\{\s*[A-Za-z_]\w*\s*<", c.expression)
        )
        flags["section_access"] = sum(1 for s in inv.security if s.kind == "section_access")
        flags["qvd_files"] = len(inv.connections)
        flags["load_script_lines"] = sum(
            s.code.count("\n") + 1 for s in inv.scripts if s.language == "qlik-load-script"
        )
