"""Power Query (M) analysis: step splitting, source detection, column flow.

There is no dependency DMV for M — the engine will not tell us what a query
does — so this module reads the script. It is explicitly *not* an M
interpreter. It tokenizes a `let ... in` block, splits it into steps, and
pattern-matches the table functions we know how to reason about. Anything
else is recorded as **opaque**: the step is kept, its name and function are
reported, and every column flowing through it is downgraded rather than
guessed at.

The output is a per-column trace: for each column we can name at the end of
the query, which source column(s) it came from and how sure we are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator

from pbilineage.models import Confidence

__all__ = [
    "ColumnLineage",
    "MQueryAnalysis",
    "MSourceRef",
    "MStep",
    "StepKind",
    "analyze_m_query",
    "split_let_steps",
    "tokenize_m",
]


class MTokenKind(StrEnum):
    IDENT = "ident"
    QUOTED_IDENT = "quoted_ident"  # #"Renamed Columns"
    STRING = "string"
    NUMBER = "number"
    OPERATOR = "operator"
    PUNCT = "punct"
    COMMENT = "comment"
    WHITESPACE = "whitespace"


@dataclass(frozen=True, slots=True)
class MToken:
    kind: MTokenKind
    value: str
    position: int

    @property
    def is_significant(self) -> bool:
        return self.kind not in (MTokenKind.WHITESPACE, MTokenKind.COMMENT)


class StepKind(StrEnum):
    SOURCE = "source"
    NAVIGATION = "navigation"
    SELECT = "select"
    REMOVE = "remove"
    RENAME = "rename"
    TRANSFORM = "transform"
    RETYPE = "retype"
    ADD = "add"
    EXPAND = "expand"
    GROUP = "group"
    UNPIVOT = "unpivot"
    PIVOT = "pivot"
    COMBINE = "combine"
    JOIN = "join"
    PASSTHROUGH = "passthrough"
    OPAQUE = "opaque"


#: table functions that neither add, drop, nor rename columns
PASSTHROUGH_FUNCTIONS = frozenset(
    {
        "Table.SelectRows",
        "Table.Sort",
        "Table.Distinct",
        "Table.Buffer",
        "Table.FirstN",
        "Table.LastN",
        "Table.Skip",
        "Table.Range",
        "Table.RemoveRowsWithErrors",
        "Table.RemoveFirstN",
        "Table.RemoveLastN",
        "Table.ReplaceValue",
        "Table.ReplaceErrorValues",
        "Table.FillDown",
        "Table.FillUp",
        "Table.PromoteHeaders",
        "Table.ReorderColumns",
        "Table.Repeat",
    }
)

#: data-source constructors we recognise, mapped to a coarse source kind
SOURCE_FUNCTIONS: dict[str, str] = {
    "Sql.Database": "Sql",
    "Sql.Databases": "Sql",
    "Value.NativeQuery": "NativeQuery",
    "Odbc.DataSource": "Odbc",
    "Odbc.Query": "Odbc",
    "OleDb.DataSource": "OleDb",
    "Oracle.Database": "Oracle",
    "PostgreSQL.Database": "PostgreSQL",
    "MySQL.Database": "MySQL",
    "Snowflake.Databases": "Snowflake",
    "AmazonRedshift.Database": "Redshift",
    "GoogleBigQuery.Database": "BigQuery",
    "Databricks.Catalogs": "Databricks",
    "Lakehouse.Contents": "Lakehouse",
    "Fabric.Warehouse": "FabricWarehouse",
    "AzureStorage.Blobs": "AzureBlob",
    "AzureStorage.DataLake": "AzureDataLake",
    "DataLake.Contents": "AzureDataLake",
    "Excel.Workbook": "Excel",
    "Csv.Document": "Csv",
    "Json.Document": "Json",
    "Xml.Tables": "Xml",
    "Web.Contents": "Web",
    "Web.BrowserContents": "Web",
    "SharePoint.Files": "SharePoint",
    "SharePoint.Tables": "SharePoint",
    "SharePoint.Contents": "SharePoint",
    "PowerPlatform.Dataflows": "Dataflow",
    "PowerBI.Dataflows": "Dataflow",
    "AnalysisServices.Database": "AnalysisServices",
    "Salesforce.Data": "Salesforce",
    "Folder.Files": "Folder",
    "File.Contents": "File",
}

_NUMBER_RE = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?")
_OPERATOR_CHARS = frozenset("+-*/&<>=@?")


def tokenize_m(text: str) -> list[MToken]:
    """Tokenize an M script. Never raises on malformed input."""
    return list(_scan_m(text or ""))


def _scan_m(text: str) -> Iterator[MToken]:
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        start = i

        if ch in " \t\r\n":
            while i < n and text[i] in " \t\r\n":
                i += 1
            yield MToken(MTokenKind.WHITESPACE, text[start:i], start)
            continue

        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            yield MToken(MTokenKind.COMMENT, text[start:i], start)
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            yield MToken(MTokenKind.COMMENT, text[start:i], start)
            continue

        # #"quoted identifier"
        if ch == "#" and i + 1 < n and text[i + 1] == '"':
            i += 2
            buf: list[str] = []
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            yield MToken(MTokenKind.QUOTED_IDENT, "".join(buf), start)
            continue

        if ch == '"':
            i += 1
            buf = []
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            yield MToken(MTokenKind.STRING, "".join(buf), start)
            continue

        if ch.isdigit():
            match = _NUMBER_RE.match(text, i)
            i = match.end() if match else i + 1
            yield MToken(MTokenKind.NUMBER, text[start:i], start)
            continue

        # #date, #datetime, #shared, #table are literal keywords
        if ch == "#" and i + 1 < n and (text[i + 1].isalpha()):
            i += 1
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            yield MToken(MTokenKind.IDENT, text[start:i], start)
            continue

        if ch.isalpha() or ch == "_":
            while i < n and (text[i].isalnum() or text[i] in "_."):
                i += 1
            yield MToken(MTokenKind.IDENT, text[start:i], start)
            continue

        if ch in _OPERATOR_CHARS:
            while i < n and text[i] in _OPERATOR_CHARS:
                i += 1
            yield MToken(MTokenKind.OPERATOR, text[start:i], start)
            continue

        i += 1
        yield MToken(MTokenKind.PUNCT, ch, start)


_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")", "]", "}"}


def _split_top_level(tokens: list[MToken], separator: str = ",") -> list[list[MToken]]:
    """Split a token list on `separator` at bracket depth zero."""
    groups: list[list[MToken]] = []
    current: list[MToken] = []
    depth = 0
    for token in tokens:
        if token.kind is MTokenKind.PUNCT:
            if token.value in _OPENERS:
                depth += 1
            elif token.value in _CLOSERS:
                depth -= 1
            elif token.value == separator and depth == 0:
                groups.append(current)
                current = []
                continue
        current.append(token)
    if current:
        groups.append(current)
    return groups


def _read_literal(tokens: list[MToken], index: int) -> tuple[Any, int]:
    """Read one literal value (string / number / list / record) at `index`.

    Returns (value, next_index). Non-literal expressions read as None so the
    caller can tell "a list of two strings" from "something computed".
    """
    if index >= len(tokens):
        return None, index
    token = tokens[index]
    if token.kind is MTokenKind.STRING:
        return token.value, index + 1
    if token.kind is MTokenKind.NUMBER:
        return token.value, index + 1
    if token.kind is MTokenKind.PUNCT and token.value == "{":
        depth = 0
        end = index
        for j in range(index, len(tokens)):
            tok = tokens[j]
            if tok.kind is MTokenKind.PUNCT and tok.value in _OPENERS:
                depth += 1
            elif tok.kind is MTokenKind.PUNCT and tok.value in _CLOSERS:
                depth -= 1
                if depth == 0:
                    end = j
                    break
        else:
            return None, len(tokens)
        inner = tokens[index + 1 : end]
        items: list[Any] = []
        for group in _split_top_level(inner):
            significant = [t for t in group if t.is_significant]
            if not significant:
                continue
            value, _ = _read_literal(significant, 0)
            items.append(value)
        return items, end + 1
    return None, index + 1


def _bracket_fields(tokens: list[MToken]) -> list[str]:
    """Field names referenced as `[Name]` inside an expression."""
    names: list[str] = []
    for i, token in enumerate(tokens):
        if token.kind is MTokenKind.PUNCT and token.value == "[":
            j = i + 1
            parts: list[str] = []
            while j < len(tokens) and not (tokens[j].kind is MTokenKind.PUNCT and tokens[j].value == "]"):
                if tokens[j].kind in (
                    MTokenKind.IDENT,
                    MTokenKind.QUOTED_IDENT,
                    MTokenKind.STRING,
                ):
                    parts.append(tokens[j].value)
                j += 1
            if parts:
                names.append(" ".join(parts) if len(parts) > 1 else parts[0])
    return names


@dataclass(slots=True)
class MSourceRef:
    """A data source (and optionally the item within it) reached by a query."""

    kind: str
    server: str = ""
    database: str = ""
    item: str = ""
    schema: str = ""
    native_query: str = ""
    function: str = ""

    def display(self) -> str:
        bits = [b for b in (self.server, self.database, self.schema, self.item) if b]
        return " / ".join(bits) or self.kind

    def connection_display(self) -> str:
        """The source itself, without the item — used when naming its tables."""
        bits = [b for b in (self.server, self.database) if b]
        return " / ".join(bits) or self.kind


@dataclass(slots=True)
class MStep:
    name: str
    kind: StepKind
    function: str = ""
    text: str = ""
    inputs: list[str] = field(default_factory=list)
    #: only set for StepKind.SOURCE
    source: MSourceRef | None = None
    note: str = ""

    @property
    def is_opaque(self) -> bool:
        return self.kind is StepKind.OPAQUE


@dataclass(slots=True)
class ColumnLineage:
    """Where one output column of a query came from."""

    name: str
    source_columns: set[str] = field(default_factory=set)
    confidence: Confidence = Confidence.HEURISTIC
    ops: list[str] = field(default_factory=list)

    def touch(self, op: str) -> None:
        if op and (not self.ops or self.ops[-1] != op):
            self.ops.append(op)


@dataclass(slots=True)
class MQueryAnalysis:
    query_name: str = ""
    steps: list[MStep] = field(default_factory=list)
    sources: list[MSourceRef] = field(default_factory=list)
    columns: dict[str, ColumnLineage] = field(default_factory=dict)
    #: True when a SelectColumns/expand told us the definitive column set
    column_set_known: bool = False
    opaque: bool = False
    unrecognized: list[str] = field(default_factory=list)
    referenced_queries: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> Confidence:
        return Confidence.OPAQUE if self.opaque else Confidence.HEURISTIC

    def lineage_for(self, column: str) -> ColumnLineage | None:
        target = column.strip().lower()
        for name, lineage in self.columns.items():
            if name.lower() == target:
                return lineage
        return None


def split_let_steps(script: str) -> list[tuple[str, list[MToken]]]:
    """Split a `let ... in ...` block into (step name, expression tokens).

    A script with no `let` is treated as a single anonymous step, which is
    what shared expressions and one-liner partitions usually are.
    """
    tokens = [t for t in tokenize_m(script) if t.is_significant]
    if not tokens:
        return []

    start = None
    for i, token in enumerate(tokens):
        if token.kind is MTokenKind.IDENT and token.value == "let":
            start = i + 1
            break
    if start is None:
        return [("Source", tokens)]

    # find the matching top-level `in`
    depth = 0
    end = len(tokens)
    for j in range(start, len(tokens)):
        token = tokens[j]
        if token.kind is MTokenKind.PUNCT and token.value in _OPENERS:
            depth += 1
        elif token.kind is MTokenKind.PUNCT and token.value in _CLOSERS:
            depth -= 1
        elif depth == 0 and token.kind is MTokenKind.IDENT and token.value == "in":
            end = j
            break

    steps: list[tuple[str, list[MToken]]] = []
    for group in _split_top_level(tokens[start:end]):
        if not group:
            continue
        # `Name = expression`
        eq_index = None
        depth = 0
        for k, token in enumerate(group):
            if token.kind is MTokenKind.PUNCT and token.value in _OPENERS:
                depth += 1
            elif token.kind is MTokenKind.PUNCT and token.value in _CLOSERS:
                depth -= 1
            elif depth == 0 and token.kind is MTokenKind.OPERATOR and token.value == "=":
                eq_index = k
                break
        if eq_index is None or eq_index == 0:
            steps.append(("", group))
            continue
        name_tokens = group[:eq_index]
        name = " ".join(t.value for t in name_tokens)
        steps.append((name, group[eq_index + 1 :]))
    return steps


def _head_function(tokens: list[MToken]) -> tuple[str, list[list[MToken]]]:
    """Return (function name, top-level argument token groups) for `F(a, b)`."""
    if len(tokens) < 2:
        return "", []
    head = tokens[0]
    if head.kind is not MTokenKind.IDENT:
        return "", []
    if not (tokens[1].kind is MTokenKind.PUNCT and tokens[1].value == "("):
        return "", []
    depth = 0
    end = len(tokens)
    for j in range(1, len(tokens)):
        token = tokens[j]
        if token.kind is MTokenKind.PUNCT and token.value in _OPENERS:
            depth += 1
        elif token.kind is MTokenKind.PUNCT and token.value in _CLOSERS:
            depth -= 1
            if depth == 0:
                end = j
                break
    return head.value, _split_top_level(tokens[2:end])


def _identifier_names(tokens: list[MToken]) -> list[str]:
    return [t.value for t in tokens if t.kind in (MTokenKind.IDENT, MTokenKind.QUOTED_IDENT)]


def _source_from_call(function: str, args: list[list[MToken]]) -> MSourceRef:
    kind = SOURCE_FUNCTIONS.get(function, "Unknown")
    literals = [
        value
        for arg in args
        for value in [_read_literal([t for t in arg if t.is_significant], 0)[0]]
        if isinstance(value, str)
    ]
    ref = MSourceRef(kind=kind, function=function)
    if function in ("Sql.Database", "Sql.Databases", "Oracle.Database", "PostgreSQL.Database"):
        ref.server = literals[0] if literals else ""
        ref.database = literals[1] if len(literals) > 1 else ""
    elif function == "Value.NativeQuery":
        # Value.NativeQuery(source, "SELECT ...", params)
        ref.native_query = literals[0] if literals else ""
    elif literals:
        ref.server = literals[0]
    return ref


def _navigation_target(tokens: list[MToken]) -> tuple[str, str]:
    """Pull (schema, item) out of `Source{[Schema="dbo",Item="Fact"]}[Data]`."""
    schema = item = ""
    for i, token in enumerate(tokens):
        if token.kind is MTokenKind.IDENT and token.value in ("Schema", "Item", "Name"):
            for j in range(i + 1, min(i + 4, len(tokens))):
                if tokens[j].kind is MTokenKind.STRING:
                    if token.value == "Schema":
                        schema = tokens[j].value
                    else:
                        item = item or tokens[j].value
                    break
    return schema, item


class _ColumnState:
    """Tracks the live column set as steps are applied."""

    def __init__(self) -> None:
        self.columns: dict[str, ColumnLineage] = {}
        self.known_set = False
        self.tainted = False

    def ensure(self, name: str) -> ColumnLineage:
        """A column named for the first time is assumed to come from the source."""
        existing = self.columns.get(name)
        if existing is not None:
            return existing
        lineage = ColumnLineage(
            name=name,
            source_columns={name},
            confidence=Confidence.OPAQUE if self.tainted else Confidence.HEURISTIC,
        )
        self.columns[name] = lineage
        return lineage

    def rename(self, old: str, new: str) -> None:
        lineage = self.ensure(old)
        del self.columns[old]
        lineage.name = new
        lineage.touch(f"rename({old} -> {new})")
        self.columns[new] = lineage

    def derive(self, new: str, inputs: list[str], op: str) -> None:
        sources: set[str] = set()
        for column in inputs:
            sources |= self.ensure(column).source_columns
        lineage = ColumnLineage(
            name=new,
            source_columns=sources,
            confidence=Confidence.OPAQUE if self.tainted else Confidence.HEURISTIC,
            ops=[op],
        )
        self.columns[new] = lineage

    def taint(self, reason: str) -> None:
        self.tainted = True
        for lineage in self.columns.values():
            lineage.confidence = Confidence.OPAQUE
            lineage.touch(reason)


def analyze_m_query(script: str, query_name: str = "") -> MQueryAnalysis:
    """Analyse one M query end to end.

    The returned analysis names the sources it reaches, the steps it takes,
    and — per output column — which source column(s) it traces back to.
    Unrecognised transforms taint the flow to `opaque` instead of dropping
    it or inventing lineage.
    """
    analysis = MQueryAnalysis(query_name=query_name)
    state = _ColumnState()
    step_names: set[str] = set()

    for name, tokens in split_let_steps(script):
        if not tokens:
            continue
        function, args = _head_function(tokens)
        text = " ".join(t.value for t in tokens)[:400]
        step = MStep(name=name or "Source", kind=StepKind.OPAQUE, function=function, text=text)
        first_arg = [t for t in args[0] if t.is_significant] if args else []
        step.inputs = [n for n in _identifier_names(first_arg) if n in step_names]

        def literal_arg(index: int) -> Any:
            if index >= len(args):
                return None
            significant = [t for t in args[index] if t.is_significant]
            value, _ = _read_literal(significant, 0)
            return value

        if function in SOURCE_FUNCTIONS:
            step.kind = StepKind.SOURCE
            step.source = _source_from_call(function, args)
            analysis.sources.append(step.source)

        elif not function and any(t.kind is MTokenKind.PUNCT and t.value == "{" for t in tokens):
            # Navigation such as Source{[Schema="dbo",Item="FactSales"]}[Data]
            schema, item = _navigation_target(tokens)
            step.kind = StepKind.NAVIGATION
            if item or schema:
                step.note = f"item={item or '?'} schema={schema or '?'}"
                if analysis.sources:
                    analysis.sources[-1].item = analysis.sources[-1].item or item
                    analysis.sources[-1].schema = analysis.sources[-1].schema or schema

        elif function == "Table.SelectColumns":
            keep = literal_arg(1)
            step.kind = StepKind.SELECT
            if isinstance(keep, list) and all(isinstance(k, str) for k in keep):
                for column in keep:
                    state.ensure(column).touch("select")
                state.columns = {k: v for k, v in state.columns.items() if k in set(keep)}
                state.known_set = True
            elif isinstance(keep, str):
                state.ensure(keep)
                state.columns = {keep: state.columns[keep]}
                state.known_set = True
            else:
                step.note = "column list is computed, not literal"

        elif function == "Table.RemoveColumns":
            drop = literal_arg(1)
            step.kind = StepKind.REMOVE
            names = [drop] if isinstance(drop, str) else drop if isinstance(drop, list) else []
            for column in names:
                if isinstance(column, str):
                    state.columns.pop(column, None)

        elif function == "Table.RenameColumns":
            pairs = literal_arg(1)
            step.kind = StepKind.RENAME
            for pair in _as_pairs(pairs):
                state.rename(pair[0], pair[1])

        elif function == "Table.TransformColumnTypes":
            step.kind = StepKind.RETYPE
            for entry in _as_pairs(literal_arg(1), allow_single=True):
                state.ensure(entry[0]).touch("retype")

        elif function == "Table.TransformColumns":
            step.kind = StepKind.TRANSFORM
            for entry in _as_pairs(literal_arg(1), allow_single=True):
                state.ensure(entry[0]).touch(f"transform({function})")

        elif function == "Table.AddColumn":
            new_name = literal_arg(1)
            step.kind = StepKind.ADD
            if isinstance(new_name, str):
                body = [t for t in args[2] if t.is_significant] if len(args) > 2 else []
                inputs = _bracket_fields(body)
                state.derive(new_name, inputs, f"add({function})")
                if not inputs:
                    state.columns[new_name].confidence = Confidence.OPAQUE
                    state.columns[new_name].touch("no column references found in expression")
                    step.note = "added column has no resolvable column inputs"

        elif function in ("Table.ExpandTableColumn", "Table.ExpandRecordColumn"):
            step.kind = StepKind.EXPAND
            base = literal_arg(1)
            fields = literal_arg(2)
            new_names = literal_arg(3)
            field_list = [f for f in (fields or []) if isinstance(f, str)]
            out_list = [n for n in (new_names or []) if isinstance(n, str)] or field_list
            if isinstance(base, str):
                state.columns.pop(base, None)
                for src, out in zip(field_list, out_list):
                    state.derive(out, [], f"expand({base}.{src})")
                    state.columns[out].source_columns = {f"{base}.{src}"}
                if field_list:
                    state.known_set = True

        elif function == "Table.Group":
            step.kind = StepKind.GROUP
            keys = literal_arg(1)
            key_list = [k for k in (keys or []) if isinstance(k, str)]
            aggregates = args[2] if len(args) > 2 else []
            agg_significant = [t for t in aggregates if t.is_significant]
            agg_names = [t.value for t in agg_significant if t.kind is MTokenKind.STRING]
            agg_inputs = _bracket_fields(agg_significant)
            kept = {k: state.ensure(k) for k in key_list}
            for agg in agg_names:
                lineage = ColumnLineage(
                    name=agg,
                    source_columns={s for column in agg_inputs for s in state.ensure(column).source_columns},
                    confidence=Confidence.OPAQUE if state.tainted else Confidence.HEURISTIC,
                    ops=[f"group aggregate({', '.join(agg_inputs) or '?'})"],
                )
                kept[agg] = lineage
            if key_list:
                state.columns = kept
                state.known_set = True

        elif function in ("Table.UnpivotOtherColumns", "Table.Unpivot"):
            step.kind = StepKind.UNPIVOT
            attribute = literal_arg(2)
            value = literal_arg(3)
            carried = literal_arg(1)
            carried_list = [c for c in (carried or []) if isinstance(c, str)]
            unpivoted = (
                [c for c in state.columns if c not in set(carried_list)]
                if function == "Table.UnpivotOtherColumns"
                else carried_list
            )
            new_state = {k: state.ensure(k) for k in carried_list}
            for label, default in ((attribute, "Attribute"), (value, "Value")):
                column = label if isinstance(label, str) else default
                lineage = ColumnLineage(
                    name=column,
                    source_columns={
                        s for c in unpivoted for s in state.columns.get(c, ColumnLineage(c)).source_columns
                    },
                    confidence=Confidence.HEURISTIC,
                    ops=[f"unpivot({function})"],
                )
                new_state[column] = lineage
            state.columns = new_state
            step.note = "unpivot: attribute/value columns carry the union of the unpivoted columns"

        elif function in ("Table.Pivot",):
            step.kind = StepKind.PIVOT
            step.note = "pivot produces a column set that depends on the data, not the script"
            state.taint("pivot")

        elif function in ("Table.Combine", "Table.NestedJoin", "Table.Join", "Table.FuzzyNestedJoin"):
            step.kind = StepKind.JOIN if "Join" in function else StepKind.COMBINE
            others = [
                name
                for arg in args
                for name in _identifier_names([t for t in arg if t.is_significant])
                if name in step_names
            ]
            step.inputs = sorted(set(step.inputs) | set(others))
            step.note = f"multi-input step: {', '.join(step.inputs) or 'unknown inputs'}"

        elif function in PASSTHROUGH_FUNCTIONS:
            step.kind = StepKind.PASSTHROUGH
            for column in _bracket_fields([t for t in tokens if t.is_significant]):
                state.ensure(column)

        elif function:
            step.kind = StepKind.OPAQUE
            step.note = f"unrecognised transform: {function}"
            analysis.unrecognized.append(function)
            analysis.opaque = True
            state.taint(f"opaque({function})")

        else:
            # A bare reference to a previous step, a record, a literal, ...
            step.kind = StepKind.PASSTHROUGH if step.inputs else StepKind.OPAQUE
            if step.kind is StepKind.OPAQUE:
                step.note = "step is not a recognised function call"

        if name:
            step_names.add(name)
        analysis.steps.append(step)

    analysis.columns = state.columns
    analysis.column_set_known = state.known_set
    analysis.referenced_queries = sorted(
        {ref for step in analysis.steps for ref in step.inputs if ref not in step_names}
    )
    return analysis


def _as_pairs(value: Any, allow_single: bool = False) -> list[tuple[str, str]]:
    """Normalise `{{"Old","New"}, ...}` (or a single `{"Old","New"}`) to pairs."""
    if not isinstance(value, list) or not value:
        return []
    if all(isinstance(item, str) for item in value):
        if len(value) >= 2:
            return [(value[0], value[1])]
        if allow_single and value:
            return [(value[0], value[0])]
        return []
    pairs: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, list) and item and isinstance(item[0], str):
            if len(item) >= 2 and isinstance(item[1], str):
                pairs.append((item[0], item[1]))
            elif allow_single:
                pairs.append((item[0], item[0]))
        elif isinstance(item, str) and allow_single:
            pairs.append((item, item))
    return pairs
