"""M (Power Query) expression index — build spec §10, Milestone 9.

The spec's pragmatic scope, deliberately: index every model's and
dataflow's M code and make it searchable, so a user can ask "which items
touch `dbo.FactSales`?" before changing a warehouse object. This is
**table/view grain, not column grain** — column-grain upstream lineage
needs a full M evaluator that follows renames, merges, expands and pivots,
and the spec calls that a separate later project.

What is parsed (lightly, not evaluated):

- `let … in` step names, so a hit can name the step it appeared in;
- data-source function calls (`Sql.Database`, `Odbc.Query`, …) with their
  literal arguments, which is where server/database/object names live;
- `Schema="dbo", Item="FactSales"` navigation pairs, the shape Power Query
  writes when you click through a SQL source.

Comments and string literals are handled by a small scanner so a table
name mentioned inside a comment is reported as a comment hit rather than a
code reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Data-source functions worth surfacing as upstream anchors.
_SOURCE_FUNCTIONS = (
    "Sql.Database",
    "Sql.Databases",
    "Odbc.DataSource",
    "Odbc.Query",
    "OleDb.DataSource",
    "Oracle.Database",
    "PostgreSQL.Database",
    "MySQL.Database",
    "Snowflake.Databases",
    "AmazonRedshift.Database",
    "GoogleBigQuery.Database",
    "Databricks.Catalogs",
    "Databricks.Query",
    "Lakehouse.Contents",
    "Fabric.Warehouse",
    "AzureStorage.Blobs",
    "AzureStorage.DataLake",
    "SharePoint.Files",
    "Web.Contents",
    "Excel.Workbook",
    "Csv.Document",
    "File.Contents",
    "Folder.Files",
    "PowerBI.Datamarts",
    "PowerPlatform.Dataflows",
    "Dataflows.Contents",
)

_SOURCE_CALL = re.compile(r"\b(" + "|".join(re.escape(f) for f in _SOURCE_FUNCTIONS) + r")\s*\(([^)]*)\)")
_NAV_PAIR = re.compile(r'\[\s*Schema\s*=\s*"([^"]+)"\s*,\s*Item\s*=\s*"([^"]+)"\s*\]')
_NAV_NAME = re.compile(r'\[\s*Name\s*=\s*"([^"]+)"')
_STEP = re.compile(r'^\s*(?:#"([^"]+)"|([A-Za-z_][\w]*))\s*=', re.MULTILINE)
_STRING_LITERAL = re.compile(r'"(?:[^"]|"")*"')


@dataclass(frozen=True)
class MSource:
    """A parsed data-source anchor inside one M expression."""

    function: str
    arguments: list[str]
    schema: str | None = None
    item: str | None = None

    def label(self) -> str:
        target = ".".join(part for part in (self.schema, self.item) if part)
        joined = ", ".join(self.arguments)
        return f"{self.function}({joined})" + (f" -> {target}" if target else "")


@dataclass
class MEntry:
    """One indexed M expression: a model partition, a shared expression,
    or a dataflow query."""

    item_id: str  # model/dataflow id
    item_name: str
    entry_name: str  # table/partition/query name
    m_code: str
    kind: str = "partition"  # partition | expression | dataflow_query
    workspace_id: str | None = None
    steps: list[str] = field(default_factory=list)
    sources: list[MSource] = field(default_factory=list)


@dataclass(frozen=True)
class MHit:
    entry: MEntry
    line_number: int
    line: str
    in_comment: bool
    step: str | None = None


def _strip_comments(code: str) -> tuple[str, set[int]]:
    """Blank out comments, returning the code and the set of line numbers
    (1-based) that contained comment text."""
    out: list[str] = []
    comment_lines: set[int] = set()
    i = 0
    line = 1
    length = len(code)
    while i < length:
        char = code[i]
        if char == "\n":
            line += 1
            out.append(char)
            i += 1
            continue
        if code.startswith("//", i):
            end = code.find("\n", i)
            end = length if end < 0 else end
            comment_lines.add(line)
            out.append(" " * (end - i))
            i = end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            end = length if end < 0 else end + 2
            for offset in range(i, end):
                if code[offset] == "\n":
                    line += 1
                    out.append("\n")
                else:
                    out.append(" ")
                comment_lines.add(line)
            i = end
            continue
        if char == '"':
            match = _STRING_LITERAL.match(code, i)
            if match:
                out.append(match.group(0))
                line += match.group(0).count("\n")
                i = match.end()
                continue
        out.append(char)
        i += 1
    return "".join(out), comment_lines


def parse_m(code: str) -> tuple[list[str], list[MSource]]:
    """Extract step names and data-source anchors from one M expression."""
    stripped, _ = _strip_comments(code)
    steps = [name or bare for name, bare in _STEP.findall(stripped)]
    sources: list[MSource] = []
    navigations = _NAV_PAIR.findall(stripped)
    names = _NAV_NAME.findall(stripped)
    for index, match in enumerate(_SOURCE_CALL.finditer(stripped)):
        function = match.group(1)
        arguments = [
            argument.strip().strip('"')
            for argument in match.group(2).split(",")
            if argument.strip() and not argument.strip().startswith("[")
        ]
        schema = item = None
        if index < len(navigations):
            schema, item = navigations[index]
        elif names:
            item = names[0]
        sources.append(MSource(function=function, arguments=arguments, schema=schema, item=item))
    # navigation pairs beyond the source calls still name real objects
    for offset in range(len(sources), len(navigations)):
        schema, item = navigations[offset]
        sources.append(MSource(function="Navigation", arguments=[], schema=schema, item=item))
    return steps, sources


class MExpressionIndex:
    """Full-text + structured index over all M code in the estate."""

    def __init__(self) -> None:
        self.entries: list[MEntry] = []

    # -- population --------------------------------------------------------

    def add(self, entry: MEntry) -> MEntry:
        entry.steps, entry.sources = parse_m(entry.m_code)
        self.entries.append(entry)
        return entry

    def add_model(self, model, *, item_id: str = "", workspace_id: str | None = None) -> None:
        """Index a Model's partitions and shared expressions (§4.3
        TMSCHEMA_EXPRESSIONS / partition query definitions)."""
        identifier = item_id or model.name
        for table in model.tables:
            for partition in table.partitions:
                if partition.expression and (partition.source_type in (None, "m", "query")):
                    self.add(
                        MEntry(
                            item_id=identifier,
                            item_name=model.name,
                            entry_name=table.name,
                            m_code=partition.expression,
                            kind="partition",
                            workspace_id=workspace_id,
                        )
                    )
        for expression in model.expressions:
            self.add(
                MEntry(
                    item_id=identifier,
                    item_name=model.name,
                    entry_name=expression.name,
                    m_code=expression.expression,
                    kind="expression",
                    workspace_id=workspace_id,
                )
            )

    def add_dataflow(self, dataflow_json: dict, *, workspace_id: str | None = None) -> None:
        """Index a Scanner-returned dataflow's queries."""
        item_id = str(dataflow_json.get("objectId") or dataflow_json.get("id", ""))
        name = dataflow_json.get("name", "")
        for entity in dataflow_json.get("entities", []) or []:
            code = entity.get("mashupExpression") or entity.get("expression") or ""
            if code:
                self.add(
                    MEntry(
                        item_id=item_id,
                        item_name=name,
                        entry_name=entity.get("name", ""),
                        m_code=code,
                        kind="dataflow_query",
                        workspace_id=workspace_id,
                    )
                )
        document = dataflow_json.get("mashupDocument") or dataflow_json.get("document")
        if document:
            self.add(
                MEntry(
                    item_id=item_id,
                    item_name=name,
                    entry_name=name,
                    m_code=document,
                    kind="dataflow_query",
                    workspace_id=workspace_id,
                )
            )

    # -- querying ----------------------------------------------------------

    def search(self, needle: str, *, case_sensitive: bool = False) -> list[MHit]:
        """Full-text search over all indexed M, one hit per matching line.

        Hits inside comments are returned but flagged, so a rename impact
        review can separate real references from mentions.
        """
        hits: list[MHit] = []
        probe = needle if case_sensitive else needle.lower()
        for entry in self.entries:
            _, comment_lines = _strip_comments(entry.m_code)
            for number, line in enumerate(entry.m_code.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if probe in haystack:
                    hits.append(
                        MHit(
                            entry=entry,
                            line_number=number,
                            line=line.strip(),
                            in_comment=number in comment_lines,
                            step=_step_for_line(entry, line),
                        )
                    )
        return hits

    def impact_of(self, object_name: str) -> list[dict]:
        """Which items would a change to this table/view affect? (C6)

        Matches the structured navigation anchors first (high confidence),
        then falls back to full-text code hits.
        """
        target = object_name.lower()
        affected: dict[tuple[str, str], dict] = {}

        for entry in self.entries:
            for source in entry.sources:
                candidates = {
                    (source.item or "").lower(),
                    f"{(source.schema or '').lower()}.{(source.item or '').lower()}".strip("."),
                }
                candidates.update(argument.lower() for argument in source.arguments)
                if target in candidates:
                    key = (entry.item_id, entry.entry_name)
                    affected.setdefault(
                        key,
                        {
                            "item_id": entry.item_id,
                            "item_name": entry.item_name,
                            "entry_name": entry.entry_name,
                            "kind": entry.kind,
                            "workspace_id": entry.workspace_id,
                            "confidence": "anchor",
                            "evidence": source.label(),
                        },
                    )

        for hit in self.search(object_name):
            if hit.in_comment:
                continue
            key = (hit.entry.item_id, hit.entry.entry_name)
            affected.setdefault(
                key,
                {
                    "item_id": hit.entry.item_id,
                    "item_name": hit.entry.item_name,
                    "entry_name": hit.entry.entry_name,
                    "kind": hit.entry.kind,
                    "workspace_id": hit.entry.workspace_id,
                    "confidence": "text",
                    "evidence": f"line {hit.line_number}: {hit.line}",
                },
            )
        return sorted(affected.values(), key=lambda row: (row["item_name"], row["entry_name"]))

    def sources(self) -> list[MSource]:
        seen: dict[str, MSource] = {}
        for entry in self.entries:
            for source in entry.sources:
                seen.setdefault(source.label(), source)
        return list(seen.values())


def _step_for_line(entry: MEntry, line: str) -> str | None:
    match = _STEP.match(line.strip() + "=") if "=" in line else None
    if match:
        return match.group(1) or match.group(2)
    for step in entry.steps:
        if line.strip().startswith(step) or line.strip().startswith(f'#"{step}"'):
            return step
    return None
