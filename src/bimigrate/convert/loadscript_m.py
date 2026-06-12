"""Qlik load script -> Power Query M converter (Section 7).

Statement classes handled:
* LOAD ... FROM file (qvd/csv/xlsx/txt)  -> Csv.Document / Excel.Workbook /
  Parquet placeholder for QVD (QVDs must be re-pointed at the source or
  landed in a Lakehouse — annotated);
* SQL SELECT / LOAD ... ; SQL SELECT     -> Sql.Database native query;
* RESIDENT loads                          -> step chains referencing the
  previous query;
* preceding loads                         -> additional transform steps;
* MAPPING + ApplyMap                      -> merge-with-default pattern note;
* JOIN/KEEP/CONCATENATE prefixes          -> Table.Join / Table.Combine;
* WHERE clauses                           -> Table.SelectRows (expression
  converted heuristically; non-trivial predicates annotated);
* STORE INTO qvd                          -> dataflow destination note;
* control flow / SUB / macros             -> unconverted-block report.

Output per script: list of MQuery (name + M text + annotations) plus an
unconverted-block report — every line is accounted for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bimigrate.parsers.qlik_script import QlikScript, QlikStatement, parse_load_script


@dataclass
class MQuery:
    name: str
    m_code: str
    annotations: list[str] = field(default_factory=list)
    confidence: float = 0.9


@dataclass
class LoadScriptResult:
    queries: list[MQuery] = field(default_factory=list)
    unconverted: list[dict] = field(default_factory=list)  # {kind, text, reason}
    annotations: list[str] = field(default_factory=list)

    @property
    def conversion_rate(self) -> float:
        total = len(self.queries) + len(self.unconverted)
        return round(len(self.queries) / total, 3) if total else 0.0


_FILE_SOURCES = {
    ".csv": ('Csv.Document(File.Contents("{path}"), [Delimiter=",", Encoding=65001])', 0.85),
    ".txt": ('Csv.Document(File.Contents("{path}"), [Delimiter="\\t", Encoding=65001])', 0.80),
    ".xlsx": ('Excel.Workbook(File.Contents("{path}"), null, true)', 0.85),
    ".xls": ('Excel.Workbook(File.Contents("{path}"), null, true)', 0.80),
    ".qvd": ("Lakehouse.Contents(null){{[workspaceId=WORKSPACE_ID]}}[Data]", 0.55),
    ".parquet": ('Parquet.Document(File.Contents("{path}"))', 0.85),
}


class LoadScriptConverter:
    def convert(self, script_text: str) -> LoadScriptResult:
        parsed = parse_load_script(script_text)
        result = LoadScriptResult()
        query_counter = 0
        last_query_name: str | None = None

        for stmt in parsed.statements:
            if stmt.kind in ("set", "let"):
                continue  # variables surfaced separately via inventory
            if stmt.kind == "section":
                if "ACCESS" in stmt.text.upper():
                    result.annotations.append(
                        "SECTION ACCESS detected: converted separately to RLS roles "
                        "(see security output); not part of the dataflow"
                    )
                continue
            if stmt.kind in ("sub", "control"):
                result.unconverted.append(
                    {
                        "kind": stmt.kind,
                        "text": _head(stmt.text),
                        "reason": "control flow / subroutines have no M equivalent; "
                        "redesign as pipeline logic or parameterized dataflows",
                    }
                )
                continue
            if stmt.kind == "directive":
                upper = stmt.text.upper()
                if upper.startswith(("CONNECT", "LIB CONNECT")):
                    result.annotations.append(
                        f"connection directive: {_head(stmt.text)} — "
                        "map to a Power Query data source credential"
                    )
                elif upper.startswith("BINARY"):
                    result.unconverted.append(
                        {
                            "kind": "binary",
                            "text": _head(stmt.text),
                            "reason": "BINARY load chains another app; flatten the chain or "
                            "land the upstream model in a Lakehouse",
                        }
                    )
                continue
            if stmt.kind == "drop":
                result.annotations.append(
                    f"{_head(stmt.text)} — staging table dropped in "
                    "script; in M simply do not load that query"
                )
                continue
            if stmt.kind == "store":
                result.annotations.append(
                    f"STORE INTO {stmt.source}: map to Dataflow Gen2 destination / "
                    "Lakehouse table (medallion pattern)"
                )
                continue
            if stmt.kind == "unknown":
                result.unconverted.append(
                    {
                        "kind": "unknown",
                        "text": _head(stmt.text),
                        "reason": "statement not recognized by the converter",
                    }
                )
                continue

            # load / select
            query_counter += 1
            name = stmt.table_label or f"Query{query_counter}"
            query = self._convert_load(stmt, name, last_query_name, parsed)
            result.queries.append(query)
            last_query_name = name
        return result

    # -- single LOAD/SELECT ------------------------------------------------

    def _convert_load(
        self, stmt: QlikStatement, name: str, previous: str | None, parsed: QlikScript
    ) -> MQuery:
        annotations: list[str] = []
        confidence = 0.85
        steps: list[tuple[str, str]] = []  # (step_name, m_expr)

        if stmt.kind == "select" or stmt.source_kind == "select":
            sql = _extract_sql(stmt.text)
            steps.append(("Source", f'Sql.Database(SERVER, DATABASE, [Query="{_m_escape(sql)}"])'))
            annotations.append("native SQL query: verify query folding and credentials")
            confidence = 0.75
        elif stmt.source_kind == "from" and stmt.source:
            ext = ("." + stmt.source.rsplit(".", 1)[-1].lower()) if "." in stmt.source else ""
            template, confidence = _FILE_SOURCES.get(ext, ('File.Contents("{path}")', 0.5))
            steps.append(("Source", template.format(path=_m_escape(stmt.source))))
            if ext == ".qvd":
                annotations.append(
                    f"QVD source {stmt.source}: QVDs are not readable by Power Query — "
                    "land the data in a Lakehouse table (medallion bronze) or re-point "
                    "at the original source"
                )
            if ext in (".csv", ".txt"):
                steps.append(("PromotedHeaders", "Table.PromoteHeaders(Source)"))
        elif stmt.source_kind == "resident" and stmt.source:
            steps.append(("Source", f'#"{stmt.source}"'))
            annotations.append(f"RESIDENT {stmt.source}: references the converted query " "of that table")
        elif stmt.source_kind == "inline":
            rows = _parse_inline(stmt.text)
            steps.append(("Source", _inline_to_m(rows)))
        elif stmt.source_kind == "autogenerate":
            m = re.search(r"AUTOGENERATE\s*\(?\s*(\S+?)\s*\)?\s*(?:WHILE|$)", stmt.text, re.IGNORECASE)
            count = m.group(1) if m else "100"
            steps.append(
                (
                    "Source",
                    f"Table.FromList(List.Numbers(1, {count}), " 'Splitter.SplitByNothing(), {"RecNo"})',
                )
            )
            confidence = 0.7
        elif stmt.source_kind == "preceding":
            steps.append(("Source", f"#\"{previous or 'PreviousQuery'}\""))
            annotations.append("preceding load: emitted as transform steps over the inner load")
            confidence = 0.7
        else:
            steps.append(("Source", "null /* unresolved source */"))
            confidence = 0.4
            annotations.append("source could not be resolved")

        prev_step = steps[-1][0]

        if stmt.fields and not any("*" in f for f in stmt.fields):
            cols = ", ".join(f'"{f}"' for f in stmt.fields if re.match(r"^[\w .%-]+$", f))
            if cols:
                steps.append(
                    (
                        "SelectedColumns",
                        f"Table.SelectColumns({prev_step}, {{{cols}}}, " "MissingField.UseNull)",
                    )
                )
                prev_step = "SelectedColumns"
            calc_fields = [f for f in stmt.fields if not re.match(r"^[\w .%-]+$", f)]
            if calc_fields:
                annotations.append(
                    f"{len(calc_fields)} computed field(s) in LOAD list need custom-column "
                    "M expressions (Qlik row functions -> Text./Date./Number. equivalents)"
                )
                confidence = min(confidence, 0.65)

        if stmt.where:
            m_cond, ok = _where_to_m(stmt.where)
            steps.append(("FilteredRows", f"Table.SelectRows({prev_step}, each {m_cond})"))
            prev_step = "FilteredRows"
            if not ok:
                annotations.append(f"WHERE clause partially translated: '{stmt.where}' — review")
                confidence = min(confidence, 0.6)

        for prefix in stmt.prefixes:
            p = prefix.lower()
            if "join" in p or "keep" in p:
                kind = (
                    "JoinKind.LeftOuter"
                    if "left" in p
                    else ("JoinKind.RightOuter" if "right" in p else "JoinKind.Inner")
                )
                steps.append(
                    (
                        "Joined",
                        f'Table.NestedJoin(#"{previous}", KEY_COLUMNS, {prev_step}, '
                        f'KEY_COLUMNS, "joined", {kind})',
                    )
                )
                prev_step = "Joined"
                annotations.append(
                    f"{prefix}: join keys are Qlik's shared field names — "
                    "KEY_COLUMNS placeholder must be filled from the model"
                )
                confidence = min(confidence, 0.6)
            elif "concatenate" in p:
                steps.append(("Combined", f'Table.Combine({{#"{previous}", {prev_step}}})'))
                prev_step = "Combined"
            elif "mapping" in p:
                annotations.append(
                    "MAPPING table: used by ApplyMap — convert usages to "
                    "Table.NestedJoin with default-on-no-match"
                )
            elif "crosstable" in p:
                steps.append(
                    (
                        "Unpivoted",
                        f"Table.UnpivotOtherColumns({prev_step}, {{QUALIFIER_COLUMNS}}, "
                        '"Attribute", "Value")',
                    )
                )
                prev_step = "Unpivoted"
                annotations.append(
                    "CROSSTABLE -> unpivot; qualifier column count from the " "prefix args must be verified"
                )
                confidence = min(confidence, 0.7)
            elif "intervalmatch" in p:
                annotations.append(
                    "INTERVALMATCH: emit a range-join (expand + filter " "start<=x<=end) — review performance"
                )
                confidence = min(confidence, 0.55)
            elif "hierarchy" in p or "generic" in p:
                annotations.append(f"{prefix}: no direct M equivalent; redesign")
                confidence = min(confidence, 0.45)

        body = ",\n    ".join(f"{step} = {expr}" for step, expr in steps)
        m_code = f"let\n    {body}\nin\n    {prev_step}"
        return MQuery(name=name, m_code=m_code, annotations=annotations, confidence=confidence)


# -- helpers ---------------------------------------------------------------


def _head(text: str, n: int = 120) -> str:
    one_line = " ".join(text.split())
    return one_line[:n] + ("..." if len(one_line) > n else "")


def _m_escape(text: str) -> str:
    return text.replace('"', '""')


def _extract_sql(text: str) -> str:
    m = re.search(r"\b(SELECT\b.*)$", text, re.IGNORECASE | re.DOTALL)
    return " ".join((m.group(1) if m else text).split())


def _parse_inline(text: str) -> list[list[str]]:
    m = re.search(r"INLINE\s*\[(.*?)\]\s*(?:\(|$)", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    lines = [ln.strip() for ln in m.group(1).strip().splitlines() if ln.strip()]
    return [re.split(r"\s*,\s*", ln) for ln in lines]


def _inline_to_m(rows: list[list[str]]) -> str:
    if not rows:
        return "#table({}, {})"
    headers = ", ".join(f'"{h}"' for h in rows[0])
    data = ", ".join("{" + ", ".join(f'"{_m_escape(v)}"' for v in row) + "}" for row in rows[1:])
    return f"#table({{{headers}}}, {{{data}}})"


def _where_to_m(cond: str) -> tuple[str, bool]:
    """Heuristic Qlik WHERE -> M each-expression; returns (m, fully_translated)."""
    ok = True
    m_cond = cond
    replacements = [
        (r"(?i)\bnot\s+isnull\s*\(\s*\[?(\w+)\]?\s*\)", r"[\1] <> null"),
        (r"(?i)\bisnull\s*\(\s*\[?(\w+)\]?\s*\)", r"[\1] = null"),
        (r"(?i)\bexists\s*\(\s*\[?(\w+)\]?\s*\)", r"true /* Exists(\1): join filter */"),
        (r"(?i)\blen\s*\(", "Text.Length("),
        (r"(?i)\bupper\s*\(", "Text.Upper("),
        (r"(?i)\blower\s*\(", "Text.Lower("),
        (r"(?i)\btrim\s*\(", "Text.Trim("),
        (r"(?i)\byear\s*\(", "Date.Year("),
        (r"(?i)\bmonth\s*\(", "Date.Month("),
        (r"(?i)\b(\w+)\s*=\s*'([^']*)'", r'[\1] = "\2"'),
        (r"(?i)\band\b", "and"),
        (r"(?i)\bor\b", "or"),
        (r"(?i)\bnot\b", "not"),
        (r"<>", "<>"),
    ]
    for pattern, repl in replacements:
        m_cond = re.sub(pattern, repl, m_cond)
    if re.search(r"(?i)exists\s*\(|\bpeek\s*\(|\bprevious\s*\(|\$\(", cond):
        ok = False
    # bare field comparisons like Amount > 100
    m_cond = re.sub(r"(?<![\[\w])([A-Za-z_]\w*)\s*(=|<>|>=|<=|>|<)\s*(\d)", r"[\1] \2 \3", m_cond)
    m_cond = m_cond.replace("<>", "<>").replace(" = ", " = ")
    return m_cond, ok
