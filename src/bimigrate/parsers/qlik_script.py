"""Qlik load-script statement parser, shared by QlikView/Qlik Sense
inventory parsers and by the load-script -> Power Query M converter.

This is a statement-level parser (not a full grammar): it splits the
script into semicolon-terminated statements outside of string literals,
classifies each statement, and extracts the pieces the platform needs:
table labels, field lists, sources (FROM/RESIDENT/INLINE/AUTOGENERATE),
prefixes (MAPPING, NOCONCATENATE, joins, CONCATENATE), variables
(SET/LET), section access, and control-flow blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class QlikStatement:
    kind: str  # load | select | set | let | store | drop | section | sub | control | directive | unknown
    text: str
    table_label: str | None = None
    prefixes: list[str] = field(default_factory=list)  # mapping, noconcatenate, left join, concatenate...
    fields: list[str] = field(default_factory=list)
    source_kind: str | None = None  # from | resident | inline | autogenerate | preceding | select
    source: str | None = None  # file path / table name / connection
    where: str | None = None
    variable: str | None = None
    value: str | None = None


@dataclass
class QlikScript:
    statements: list[QlikStatement] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    tables: list[str] = field(default_factory=list)
    has_section_access: bool = False
    has_macros: bool = False
    qvd_reads: list[str] = field(default_factory=list)
    qvd_writes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


_COMMENT_LINE_RE = re.compile(r"^\s*(//|REM\b).*$", re.IGNORECASE | re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_PREFIX_RE = re.compile(
    r"^(?P<prefixes>(?:(?:MAPPING|NOCONCATENATE|BUFFER(?:\s*\([^)]*\))?|ADD|REPLACE|"
    r"(?:LEFT|RIGHT|INNER|OUTER)\s+(?:JOIN|KEEP)(?:\s*\([^)]*\))?|JOIN(?:\s*\([^)]*\))?|"
    r"KEEP(?:\s*\([^)]*\))?|CONCATENATE(?:\s*\([^)]*\))?|CROSSTABLE\s*\([^)]*\)|"
    r"INTERVALMATCH\s*\([^)]*\)|GENERIC|HIERARCHY\s*\([^)]*\)|FIRST\s+\d+)\s+)*)"
    r"(?P<verb>LOAD|SELECT|SQL\s+SELECT)\b",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(r"^\s*(?:\[(?P<b>[^\]]+)\]|\"(?P<q>[^\"]+)\"|(?P<p>[A-Za-z_][\w.]*))\s*:\s*$")
_FROM_RE = re.compile(
    r"\bFROM\s+(?:\[(?P<b>[^\]]+)\]|'(?P<s>[^']+)'|(?P<p>[^\s(;]+))(?:\s*\((?P<fmt>[^)]*)\))?",
    re.IGNORECASE,
)
_RESIDENT_RE = re.compile(r"\bRESIDENT\s+(?:\[(?P<b>[^\]]+)\]|(?P<p>[A-Za-z_][\w.]*))", re.IGNORECASE)
_WHERE_RE = re.compile(
    r"\bWHERE\s+(?P<cond>.+?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|$)", re.IGNORECASE | re.DOTALL
)
_STORE_RE = re.compile(
    r"^STORE\s+(?:\[(?P<tb>[^\]]+)\]|(?P<tp>[A-Za-z_][\w.]*))\s+INTO\s+"
    r"(?:\[(?P<fb>[^\]]+)\]|'(?P<fs>[^']+)'|(?P<fp>[^\s(;]+))",
    re.IGNORECASE,
)
_SETLET_RE = re.compile(
    r"^(?P<verb>SET|LET)\s+(?P<name>[\w.#$]+)\s*=\s*(?P<val>.*)$", re.IGNORECASE | re.DOTALL
)


def _strip_comments(script: str) -> str:
    script = _BLOCK_COMMENT_RE.sub("", script)
    return _COMMENT_LINE_RE.sub("", script)


def split_statements(script: str) -> list[str]:
    """Split on ';' that are outside single/double-quoted strings and brackets."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in script:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_fields(body: str) -> list[str]:
    """Field list between the LOAD/SELECT verb and FROM/RESIDENT/INLINE/etc."""
    cut = re.split(
        r"\bFROM\b|\bRESIDENT\b|\bINLINE\b|\bAUTOGENERATE\b", body, maxsplit=1, flags=re.IGNORECASE
    )[0]
    fields: list[str] = []
    depth = 0
    current: list[str] = []
    quote: str | None = None
    for ch in cut:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current and "".join(current).strip():
        fields.append("".join(current).strip())
    cleaned = []
    for f in fields:
        m = re.search(r"\bAS\s+(?:\[(?P<b>[^\]]+)\]|\"(?P<q>[^\"]+)\"|(?P<p>[\w.%]+))\s*$", f, re.IGNORECASE)
        if m:
            cleaned.append(m.group("b") or m.group("q") or m.group("p"))
        else:
            cleaned.append(f.strip('[]" '))
    return [c for c in cleaned if c and c != "*"]


def parse_load_script(script: str) -> QlikScript:
    result = QlikScript()
    text = _strip_comments(script)

    if re.search(r"^\s*SECTION\s+ACCESS\b", text, re.IGNORECASE | re.MULTILINE):
        result.has_section_access = True
    if re.search(r"\bSUB\s+\w+|\bCALL\s+\w+", text, re.IGNORECASE) and re.search(
        r"CreateObject|ActiveDocument|Macro", text, re.IGNORECASE
    ):
        result.has_macros = True

    pending_label: str | None = None
    for raw in split_statements(text):
        # a "Label:" line can be fused to the front of the statement
        label_match = re.match(
            r"^\s*(?:\[(?P<b>[^\]]+)\]|\"(?P<q>[^\"]+)\"|(?P<p>[A-Za-z_][\w.]*))\s*:\s*\n",
            raw,
        )
        if label_match and not re.match(r"^\s*(SET|LET)\b", raw, re.IGNORECASE):
            pending_label = label_match.group("b") or label_match.group("q") or label_match.group("p")
            raw = raw[label_match.end() :].strip()
        single = _LABEL_RE.match(raw)
        if single:
            pending_label = single.group("b") or single.group("q") or single.group("p")
            continue

        stmt = _classify_statement(raw, pending_label, result)
        pending_label = None
        result.statements.append(stmt)

    return result


def _classify_statement(raw: str, label: str | None, result: QlikScript) -> QlikStatement:
    upper = raw.upper().lstrip()

    m = _SETLET_RE.match(raw.strip())
    if m:
        name, val = m.group("name"), m.group("val").strip().strip("'\"")
        result.variables[name] = val
        return QlikStatement(kind=m.group("verb").lower(), text=raw, variable=name, value=val)

    m = _STORE_RE.match(raw.strip())
    if m:
        target = m.group("fb") or m.group("fs") or m.group("fp") or ""
        if target.lower().endswith(".qvd"):
            result.qvd_writes.append(target)
        return QlikStatement(
            kind="store", text=raw, source=target, table_label=m.group("tb") or m.group("tp")
        )

    if upper.startswith("SECTION "):
        return QlikStatement(kind="section", text=raw)
    if upper.startswith(("SUB ", "END SUB", "CALL ")):
        return QlikStatement(kind="sub", text=raw)
    if upper.startswith(
        (
            "IF ",
            "ELSE",
            "END IF",
            "ELSEIF",
            "FOR ",
            "NEXT",
            "DO ",
            "LOOP",
            "EXIT ",
            "WHEN ",
            "SWITCH ",
            "END SWITCH",
        )
    ):
        return QlikStatement(kind="control", text=raw)
    if upper.startswith(("DROP TABLE", "DROP TABLES", "DROP FIELD", "DROP FIELDS")):
        return QlikStatement(kind="drop", text=raw)
    if upper.startswith(
        (
            "DIRECTORY",
            "CONNECT",
            "LIB CONNECT",
            "DISCONNECT",
            "QUALIFY",
            "UNQUALIFY",
            "RENAME",
            "ALIAS",
            "TAG",
            "UNTAG",
            "TRACE",
            "SLEEP",
            "BINARY",
            "INCLUDE",
            "MUST_INCLUDE",
        )
    ):
        return QlikStatement(kind="directive", text=raw)

    m = _PREFIX_RE.match(raw.strip())
    if m:
        prefixes = [p.strip().lower() for p in re.split(r"\s{2,}|\n", m.group("prefixes")) if p.strip()]
        if m.group("prefixes").strip():
            prefixes = [m.group("prefixes").strip().lower()]
        verb = m.group("verb").upper()
        body = raw.strip()[m.end() :]
        stmt = QlikStatement(
            kind="load" if verb == "LOAD" else "select",
            text=raw,
            table_label=label,
            prefixes=prefixes,
            fields=_extract_fields(body),
        )
        if fm := _FROM_RE.search(raw):
            stmt.source_kind = "from"
            stmt.source = fm.group("b") or fm.group("s") or fm.group("p")
            if stmt.source and stmt.source.lower().endswith(".qvd"):
                result.qvd_reads.append(stmt.source)
        elif rm := _RESIDENT_RE.search(raw):
            stmt.source_kind = "resident"
            stmt.source = rm.group("b") or rm.group("p")
        elif re.search(r"\bINLINE\b", raw, re.IGNORECASE):
            stmt.source_kind = "inline"
        elif re.search(r"\bAUTOGENERATE\b", raw, re.IGNORECASE):
            stmt.source_kind = "autogenerate"
        elif stmt.kind == "load" and re.search(r"\bLOAD\b", body, re.IGNORECASE):
            stmt.source_kind = "preceding"
        if wm := _WHERE_RE.search(raw):
            stmt.where = " ".join(wm.group("cond").split())
        if label and stmt.kind in ("load", "select") and label not in result.tables:
            result.tables.append(label)
        return stmt

    return QlikStatement(kind="unknown", text=raw, table_label=label)
