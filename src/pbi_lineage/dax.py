"""DAX reference extraction via a real tokenizer (build spec §6).

Not regex — the scanner understands string literals (`""` escaping),
comments (`--`, `//`, `/* */`), quoted table names (`'...'` with `''`
escaping), bracket identifiers (`[...]` with `]]` escaping), and `VAR`
scoping, so bracket characters inside strings or comments are never
mistaken for references and locals are never mistaken for tables.

Extraction is deliberately context-free about *what* a reference is:

- `Table[X]` / `'T name'[X]` → a qualified field: column **or** measure
  (DAX allows qualified measure refs — the RDL trap in §5.4.5).
- `[X]` → a bare field: measure, or a same-table column inside a
  calculated column.
- a bare/quoted identifier not followed by `[` or `(` → a *candidate*
  table reference (`COUNTROWS(Sales)`).

`resolve_refs` settles each against a Model; unknown names are returned,
not guessed — a wrong "unused" is worse than no answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pbi_lineage.schema import Model

_WILDCARD_FUNCTIONS = {
    "SELECTEDMEASURE",
    "SELECTEDMEASURENAME",
    "ISSELECTEDMEASURE",
    "SELECTEDMEASUREFORMATSTRING",
}

_KEYWORDS = {
    "VAR",
    "RETURN",
    "EVALUATE",
    "DEFINE",
    "MEASURE",
    "ORDER",
    "BY",
    "START",
    "AT",
    "ASC",
    "DESC",
    "TRUE",
    "FALSE",
    "NOT",
    "IN",
    "AND",
    "OR",
}


@dataclass(frozen=True)
class DaxRef:
    """One field reference. `table` None = bare `[X]`; `name` None = a
    whole-table reference."""

    table: str | None
    name: str | None


@dataclass
class DaxAnalysis:
    fields: list[DaxRef] = field(default_factory=list)  # qualified + bare field refs
    tables: list[str] = field(default_factory=list)  # candidate table refs
    functions: list[str] = field(default_factory=list)
    wildcard_measures: bool = False  # SELECTEDMEASURE() family present
    errors: list[str] = field(default_factory=list)


def _is_ident_start(char: str) -> bool:
    return char.isalpha() or char == "_"


def _is_ident_char(char: str) -> bool:
    return char.isalnum() or char in "_."


def analyze(dax: str) -> DaxAnalysis:  # noqa: PLR0912, PLR0915 — a scanner is a big switch
    out = DaxAnalysis()
    if not dax:
        return out
    locals_seen: set[str] = set()
    pending_table: str | None = None  # identifier that may qualify a following [
    pending_is_quoted = False
    expect_var_name = False
    length = len(dax)
    i = 0

    def flush_pending() -> None:
        nonlocal pending_table, pending_is_quoted
        if pending_table is not None:
            upper = pending_table.upper()
            if pending_is_quoted:
                out.tables.append(pending_table)
            elif upper not in _KEYWORDS and pending_table not in locals_seen:
                out.tables.append(pending_table)
            pending_table = None
            pending_is_quoted = False

    while i < length:
        char = dax[i]

        if char in " \t\r\n":
            i += 1
            continue

        # comments -------------------------------------------------------
        if dax.startswith("--", i) or dax.startswith("//", i):
            flush_pending()
            end = dax.find("\n", i)
            i = length if end < 0 else end + 1
            continue
        if dax.startswith("/*", i):
            flush_pending()
            end = dax.find("*/", i + 2)
            if end < 0:
                out.errors.append("unterminated block comment")
                break
            i = end + 2
            continue

        # string literal ("" escapes) -------------------------------------
        if char == '"':
            flush_pending()
            i += 1
            while i < length:
                if dax[i] == '"':
                    if i + 1 < length and dax[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                out.errors.append("unterminated string literal")
            continue

        # quoted table name ('' escapes) ----------------------------------
        if char == "'":
            flush_pending()
            i += 1
            start = i
            parts: list[str] = []
            while i < length:
                if dax[i] == "'":
                    if i + 1 < length and dax[i + 1] == "'":
                        parts.append(dax[start:i] + "'")
                        i += 2
                        start = i
                        continue
                    parts.append(dax[start:i])
                    i += 1
                    break
                i += 1
            else:
                out.errors.append("unterminated quoted identifier")
                break
            pending_table = "".join(parts)
            pending_is_quoted = True
            expect_var_name = False
            continue

        # bracket identifier (]] escapes) ---------------------------------
        if char == "[":
            i += 1
            start = i
            parts = []
            while i < length:
                if dax[i] == "]":
                    if i + 1 < length and dax[i + 1] == "]":
                        parts.append(dax[start:i] + "]")
                        i += 2
                        start = i
                        continue
                    parts.append(dax[start:i])
                    i += 1
                    break
                i += 1
            else:
                out.errors.append("unterminated bracket identifier")
                break
            name = "".join(parts)
            if pending_table is not None:
                out.fields.append(DaxRef(table=pending_table, name=name))
                pending_table = None
                pending_is_quoted = False
            else:
                out.fields.append(DaxRef(table=None, name=name))
            continue

        # identifier ------------------------------------------------------
        if _is_ident_start(char):
            flush_pending()
            start = i
            while i < length and _is_ident_char(dax[i]):
                i += 1
            ident = dax[start:i]
            upper = ident.upper()
            # peek past whitespace for ( or [
            j = i
            while j < length and dax[j] in " \t\r\n":
                j += 1
            next_char = dax[j] if j < length else ""
            if expect_var_name:
                locals_seen.add(ident)
                expect_var_name = False
                continue
            if next_char == "(":
                out.functions.append(upper)
                if upper in _WILDCARD_FUNCTIONS:
                    out.wildcard_measures = True
                continue
            if next_char == "[":
                pending_table = ident
                pending_is_quoted = False
                continue
            if upper == "VAR":
                expect_var_name = True
                continue
            pending_table = ident
            pending_is_quoted = False
            flush_pending()
            continue

        flush_pending()
        i += 1

    flush_pending()
    return out


# ---------------------------------------------------------------------------
# Resolution against a model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedRef:
    kind: str  # "column" | "measure" | "table"
    table: str | None
    name: str | None


@dataclass
class Resolution:
    refs: list[ResolvedRef] = field(default_factory=list)
    unknown: list[DaxRef] = field(default_factory=list)
    wildcard_measures: bool = False


_AUTO_DATE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_", "NewLocalDateTable_")


def resolve_refs(analysis: DaxAnalysis, model: Model, *, host_table: str | None = None) -> Resolution:
    """Settle extracted references against the model.

    `host_table` is the table a calculated column lives on — its bare
    `[X]` refs resolve to same-table columns before measures. A qualified
    `T[X]` checks measures first (the RDL `Table[Measure]` trap), then
    columns, then hierarchies. Unresolvable refs land in `unknown`.
    """
    result = Resolution(wildcard_measures=analysis.wildcard_measures)
    measures_by_name: dict[str, str] = {}
    for table in model.tables:
        for measure in table.measures:
            measures_by_name.setdefault(measure.name, table.name)

    seen: set[ResolvedRef] = set()

    def emit(ref: ResolvedRef) -> None:
        if ref not in seen:
            seen.add(ref)
            result.refs.append(ref)

    for ref in analysis.fields:
        if ref.table is not None:
            table = model.table(ref.table)
            if table is None:
                result.unknown.append(ref)
                continue
            if any(m.name == ref.name for m in table.measures):
                emit(ResolvedRef("measure", table.name, ref.name))
            elif any(c.name == ref.name for c in table.columns):
                emit(ResolvedRef("column", table.name, ref.name))
            elif any(h.name == ref.name for h in table.hierarchies):
                emit(ResolvedRef("table", table.name, None))  # hierarchy use marks the table
            else:
                result.unknown.append(ref)
        else:
            host = model.table(host_table) if host_table else None
            if host is not None and any(c.name == ref.name for c in host.columns):
                emit(ResolvedRef("column", host.name, ref.name))
            elif ref.name in measures_by_name:
                emit(ResolvedRef("measure", measures_by_name[ref.name], ref.name))
            else:
                # A bare `[Month]` in a measure is most often a column the
                # author left unqualified. Resolve it when exactly one
                # authored table has that name; ambiguity, or a name only
                # Power BI's hidden date tables carry, stays unknown.
                hosts = [
                    table.name
                    for table in model.tables
                    if not table.name.startswith(_AUTO_DATE_PREFIXES)
                    and any(c.name == ref.name for c in table.columns)
                ]
                if len(hosts) == 1:
                    emit(ResolvedRef("column", hosts[0], ref.name))
                else:
                    result.unknown.append(ref)

    for name in analysis.tables:
        table = model.table(name)
        if table is not None:
            emit(ResolvedRef("table", table.name, None))
        # unknown bare identifiers are usually keywords/locals — not errors

    return result
