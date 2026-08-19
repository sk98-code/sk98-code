"""Column-grain tracing through Power Query (M).

The build spec scopes column-level *upstream* lineage out as "a separate
later project" because following a column through renames, merges,
expands, pivots and custom columns needs to understand M, not just index
it. This module is that project, done for the tractable subset.

Approach: simulate the `let … in` steps forward over an **open** column
set. The source table's real columns are unknown offline, so any name a
step mentions is taken to exist; each step then rewrites the mapping
`current name -> origin`. What survives to the final step is the model's
column list, each carrying where it came from.

Every origin records how it was derived, and anything the simulation
cannot follow is marked `unresolved` rather than guessed — an invented
lineage is worse than an absent one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- step-level patterns ---------------------------------------------------

_STEP_LINE = re.compile(
    # The `in` terminator must be a standalone keyword on its own line —
    # without the leading boundary it also matches inside `Table.NestedJoin`,
    # truncating the step body.
    r'^\s*(?:#"(?P<q>[^"]+)"|(?P<b>[A-Za-z_][\w]*))\s*=\s*(?P<body>.+?)'
    r'(?=,\s*\n\s*(?:#"|[A-Za-z_][\w]*\s*=)|\n\s*\bin\b|$)',
    re.S | re.M,
)
_NAV = re.compile(r'\{\s*\[\s*(?:Schema\s*=\s*"(?P<schema>[^"]*)"\s*,\s*)?Item\s*=\s*"(?P<item>[^"]+)"', re.S)
_NAV_NAME = re.compile(r'\{\s*\[\s*Name\s*=\s*"(?P<item>[^"]+)"', re.S)
_PAIR = re.compile(r'\{\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\}')
_QUOTED = re.compile(r'"((?:[^"]|"")*)"')
# `#"Previous Step"` is a *reference to a step*, not a column literal.
# Stripping these first is what keeps step names out of the column list.
_STEP_REF = re.compile(r'#"(?:[^"]|"")*"')


def _literals(body: str) -> list[str]:
    """Quoted literals in a step body, excluding step references."""
    return [_unquote(x) for x in _QUOTED.findall(_STEP_REF.sub(" ", body))]


_FIELD = re.compile(r"\[([^\[\]]+)\]")
_NATIVE = re.compile(r'Value\.NativeQuery\s*\(\s*[^,]+,\s*"((?:[^"]|"")*)"', re.S)
_SQL_NATIVE_ARG = re.compile(r'\[\s*Query\s*=\s*"((?:[^"]|"")*)"', re.S)


@dataclass
class ColumnOrigin:
    """Where one model column came from."""

    name: str
    source_table: str | None = None
    source_column: str | None = None
    derivation: str = "traced"  # traced | renamed | computed | native-sql | unresolved
    steps: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.source_column is not None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "source_table": self.source_table,
            "source_column": self.source_column,
            "derivation": self.derivation,
            "detail": self.detail,
            "steps": self.steps,
        }


@dataclass
class TraceResult:
    table: str
    source_table: str | None = None
    source_schema: str | None = None
    columns: dict[str, ColumnOrigin] = field(default_factory=dict)
    unsupported_steps: list[str] = field(default_factory=list)

    def origin(self, column: str) -> ColumnOrigin | None:
        return self.columns.get(column)


def _unquote(text: str) -> str:
    return text.replace('""', '"')


def _split_steps(m_code: str) -> list[tuple[str, str]]:
    """(step name, step body) in source order."""
    body = m_code
    start = body.find("let")
    if start >= 0:
        body = body[start + 3 :]
    end = re.search(r"\bin\b\s*[^\n]*$", body)
    if end:
        body = body[: end.start()]
    steps: list[tuple[str, str]] = []
    for match in _STEP_LINE.finditer(body):
        name = match.group("q") or match.group("b")
        steps.append((name, match.group("body").strip().rstrip(",")))
    return steps


def _sql_output_columns(sql: str) -> list[tuple[str, str]]:
    """(output name, source name) for a simple `SELECT a, b AS c` list.

    Deliberately conservative: `SELECT *`, subqueries, and anything with a
    function call in the projection yield nothing, so the caller falls back
    to 'unresolved' instead of inventing a mapping.
    """
    match = re.search(r"\bSELECT\b(?P<cols>.+?)\bFROM\b", sql, re.I | re.S)
    if not match:
        return []
    columns = match.group("cols")
    if "*" in columns:
        return []
    out: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for char in columns + ",":
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += char
    pairs: list[tuple[str, str]] = []
    for item in out:
        text = item.strip().strip('"').strip("[]")
        if not text or "(" in text:
            continue
        alias_match = re.match(r"^(?P<src>[\w.\"\[\]]+)\s+(?:AS\s+)?(?P<alias>[\w\"\[\]]+)$", text, re.I)
        if alias_match:
            source = alias_match.group("src").split(".")[-1].strip('"[]')
            pairs.append((alias_match.group("alias").strip('"[]'), source))
        else:
            plain = text.split(".")[-1].strip('"[]')
            if re.fullmatch(r"\w+", plain):
                pairs.append((plain, plain))
    return pairs


def _sql_from_table(sql: str) -> str | None:
    match = re.search(r"\bFROM\b\s+([\w.\"\[\]]+)", sql, re.I)
    if not match:
        return None
    return match.group(1).replace('"', "").replace("[", "").replace("]", "")


def trace_table(m_code: str, table_name: str) -> TraceResult:  # noqa: PLR0912, PLR0915
    """Trace every column of one table back through its M."""
    result = TraceResult(table=table_name)
    columns: dict[str, ColumnOrigin] = {}
    open_base = True  # the source table's real column list is unknown offline

    def ensure(name: str) -> ColumnOrigin:
        if name not in columns:
            columns[name] = ColumnOrigin(
                name=name,
                source_table=result.source_table,
                source_column=name,
                derivation="traced",
            )
        return columns[name]

    for step_name, body in _split_steps(m_code):
        # --- source / navigation ---------------------------------------
        nav = _NAV.search(body) or _NAV_NAME.search(body)
        if nav:
            groups = nav.groupdict()
            result.source_schema = groups.get("schema") or result.source_schema
            item = groups.get("item")
            if item:
                result.source_table = ".".join(p for p in (result.source_schema, item) if p)
            continue

        native = _NATIVE.search(body) or _SQL_NATIVE_ARG.search(body)
        if native:
            sql = _unquote(native.group(1))
            result.source_table = _sql_from_table(sql) or result.source_table
            pairs = _sql_output_columns(sql)
            if pairs:
                columns = {
                    alias: ColumnOrigin(
                        name=alias,
                        source_table=result.source_table,
                        source_column=source,
                        derivation="native-sql",
                        steps=[step_name],
                        detail=f"SELECT {source}" + (f" AS {alias}" if alias != source else ""),
                    )
                    for alias, source in pairs
                }
                open_base = False
            else:
                result.unsupported_steps.append(f"{step_name}: native SQL projection not parseable")
            continue

        # --- renames ----------------------------------------------------
        if "Table.RenameColumns" in body:
            for old, new in _PAIR.findall(body):
                origin = columns.pop(old, None) or (
                    ColumnOrigin(
                        name=old,
                        source_table=result.source_table,
                        source_column=old,
                        derivation="traced",
                    )
                    if open_base
                    else None
                )
                if origin is None:
                    origin = ColumnOrigin(name=old, derivation="unresolved")
                origin.name = new
                origin.derivation = "renamed" if origin.resolved else origin.derivation
                origin.steps = origin.steps + [step_name]
                origin.detail = f"renamed from {old}"
                columns[new] = origin
            continue

        # --- added / computed columns -----------------------------------
        if "Table.AddColumn" in body:
            names = _literals(body)
            new_name = names[0] if names else None
            if new_name:
                inputs = sorted({f for f in _FIELD.findall(body) if f != new_name})
                origin = ColumnOrigin(
                    name=new_name,
                    source_table=result.source_table,
                    source_column=None,
                    derivation="computed",
                    steps=[step_name],
                    detail="computed from " + (", ".join(inputs) if inputs else "a constant"),
                )
                # a computed column inherits the origin when it is a pure
                # pass-through of exactly one column
                if (
                    len(inputs) == 1
                    and inputs[0] in columns
                    and re.fullmatch(
                        r"[^=]*each\s*\[\s*" + re.escape(inputs[0]) + r"\s*\]\s*\)?\s*",
                        body.split(",", 2)[-1],
                    )
                ):
                    parent = columns[inputs[0]]
                    origin.source_column = parent.source_column
                    origin.derivation = parent.derivation
                columns[new_name] = origin
            continue

        # --- projections -------------------------------------------------
        if "Table.SelectColumns" in body or "Table.RemoveColumns" in body:
            listed = _literals(body)
            if "Table.SelectColumns" in body:
                for name in listed:
                    ensure(name)
                columns = {k: v for k, v in columns.items() if k in listed}
                open_base = False
            else:
                for name in listed:
                    columns.pop(name, None)
            continue

        if "Table.ExpandTableColumn" in body:
            listed = _literals(body)
            # first literal is the column being expanded; the rest are fields
            for name in listed[1:]:
                origin = ensure(name)
                origin.steps = origin.steps + [step_name]
                origin.detail = f"expanded from {listed[0]}" if listed else "expanded"
            continue

        # --- transformations that keep the column identity ---------------
        if any(
            token in body
            for token in (
                "Table.SelectRows",
                "Table.TransformColumnTypes",
                "Table.TransformColumns",
                "Table.ReorderColumns",
                "Table.Sort",
                "Table.Distinct",
                "Table.Buffer",
                "Table.FirstN",
                "Table.Skip",
            )
        ):
            for name in set(_literals(body)):
                if name in columns:
                    columns[name].steps = columns[name].steps + [step_name]
            continue

        # --- anything we cannot follow ------------------------------------
        if any(
            token in body
            for token in (
                "Table.Join",
                "Table.NestedJoin",
                "Table.Combine",
                "Table.Pivot",
                "Table.Unpivot",
                "Table.Group",
                "Table.FromRecords",
            )
        ):
            result.unsupported_steps.append(f"{step_name}: {body.split('(')[0].strip()} not traced")

    result.columns = columns
    return result


def trace_model(model, index) -> dict[str, TraceResult]:
    """Trace every table in a model that has M behind it."""
    traces: dict[str, TraceResult] = {}
    for table in model.tables:
        code = ""
        for entry in index.entries:
            if entry.kind == "partition" and entry.entry_name == table.name:
                code = entry.m_code
                break
        if not code:
            continue
        trace = trace_table(code, table.name)
        # keep only columns the model actually has, and report the rest as
        # model columns with no traceable origin
        for column in table.columns:
            if column.name not in trace.columns:
                trace.columns[column.name] = ColumnOrigin(
                    name=column.name,
                    source_table=trace.source_table,
                    source_column=None,
                    derivation="calculated" if column.expression else "unresolved",
                    detail=(
                        "calculated column (DAX)" if column.expression else "no M step named this column"
                    ),
                )
        model_columns = {c.name for c in table.columns}
        trace.columns = {k: v for k, v in trace.columns.items() if k in model_columns}
        traces[table.name] = trace
    return traces
