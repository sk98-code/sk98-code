"""Capture a redacted diagnostic bundle from a live tenant.

The point of this module is to make it safe to share what a real tenant's
API responses *look like* without sharing what they contain. It answers
"is my reading of the API contract right?" and nothing else.

What is preserved (this is the contract, and the whole reason to capture):

* every JSON **key**, and the nesting around it
* every **API vocabulary value** — `columnType: "Calculated"`,
  `datasourceType: "Sql"`, `state: "Active"`, scan statuses, and so on
* the **structure** of M and DAX expressions: function calls, step order,
  argument shapes

What is replaced (consistently, so cross-references still line up):

* names of workspaces, models, tables, columns, measures and dataflows
* servers, databases, paths and URLs
* email addresses and object GUIDs
* free-text descriptions, which are dropped rather than pseudonymized

A name maps to the same pseudonym everywhere it appears, including inside
expression text, so

    Table.RenameColumns(#"Removed Columns", {{"SalesAmount", "Amount"}})

survives as

    Table.RenameColumns(#"Name36", {{"Name37", "Name7"}})

— still readable as a rename of one known column to another, and still
parseable by `parsers.m_query` into exactly the same steps.

Expression text gets structural treatment rather than substring replacement
(`scrub_expression`): substring replacement would rewrite `Amount` inside
`SalesAmount` and leak the `Sales` half, and an over-eager hostname sweep
would turn `Sql.Database` into a pseudonym. Both are regression-tested.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pbilineage import __version__
from pbilineage.clients.admin_api import PowerBIAdminClient
from pbilineage.clients.http import ApiError
from pbilineage.clients.xmla import XmlaClient, XmlaUnavailable
from pbilineage.config import Settings
from pbilineage.resolve.xmla_resolver import CALC_DEPENDENCY_QUERY
from pbilineage.scan.normalize import snapshot_from_scan_results

__all__ = ["Scrubber", "capture_bundle"]

#: keys whose values are identity-bearing and get pseudonymized
IDENTITY_KEYS = {
    "name": "Name",
    "displayname": "Name",
    "server": "Server",
    "database": "Database",
    "url": "Url",
    "path": "Path",
    "account": "Account",
    "domain": "Domain",
    "connectionstring": "ConnectionString",
    "configuredby": "User",
    "createdby": "User",
    "modifiedby": "User",
    "datasetuserAccessright": None,
    "emailaddress": "User",
    "userprincipalname": "User",
}

#: keys whose values are Microsoft's vocabulary, never customer data
CONTRACT_KEYS = {
    "columntype",
    "datasourcetype",
    "datatype",
    "type",
    "state",
    "status",
    "mode",
    "kind",
    "version",
    "isondedicatedcapacity",
    "ishidden",
    "sku",
}

#: keys whose values are free text with no structural value — dropped outright
DROPPED_KEYS = {"description", "note", "comment"}

#: keys holding M or DAX source, which is scrubbed structurally rather than by
#: substring replacement (see `Scrubber.scrub_expression`)
EXPRESSION_KEYS = {"expression", "querydefinition"}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_GUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")

#: A hostname, distinguished from a dotted M/DAX identifier by case: hosts are
#: lowercase (`finance-sql.database.windows.net`), while the M standard library
#: is PascalCase (`Sql.Database`, `Table.SelectColumns`). Without this the
#: sweep would rewrite the very function names the capture exists to show.
_HOST = re.compile(r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\b")

#: The name-bearing positions in M and DAX source. Anything matched here is a
#: value the author chose — a step name, a string literal, a bracketed column
#: or measure — as opposed to language the parser needs to still recognise.
#: Bracket matching deliberately excludes `=` and `"` so that an M record such
#: as `[Schema="dbo",Item="Sales"]` is left to the string-literal rule instead
#: of being swallowed whole.
_M_QUOTED_IDENT = r'#"(?P<step>(?:[^"]|"")*)"'
_STRING_LITERAL = r'"(?P<literal>(?:[^"]|"")*)"'
_BRACKET_IDENT = r"\[(?P<bracket>[^\]\"=]+)\]"
_BARE_IDENT = r"(?P<ident>[A-Za-z_][A-Za-z0-9_]*)"
_EXPRESSION_TOKEN = re.compile("|".join((_M_QUOTED_IDENT, _STRING_LITERAL, _BRACKET_IDENT, _BARE_IDENT)))

#: literals that are language, not data, and must survive verbatim
_M_TYPE_WORDS = frozenset({"true", "false", "null", "type", "each", "let", "in", "meta", "as", "is", "error"})


@dataclass
class Scrubber:
    """Structure-preserving pseudonymizer with a stable, per-run name map.

    Identity discovery happens entirely in `register`; `scrub` only applies
    the map that pass produced. Keeping the two apart is what makes the
    result deterministic: a rewrite never discovers a new name, so it can
    never rewrite its own output or assign one value two pseudonyms.
    """

    #: real value -> pseudonym, so the same name maps the same way everywhere
    mapping: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    enabled: bool = True
    _pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _loose: re.Pattern[str] | None = field(default=None, init=False, repr=False)

    def pseudonym(self, value: str, category: str = "Name") -> str:
        if not value:
            return value
        existing = self.mapping.get(value)
        if existing is not None:
            return existing
        self.counters[category] = self.counters.get(category, 0) + 1
        if category == "User":
            replacement = f"user{self.counters[category]}@example.invalid"
        elif category == "Guid":
            replacement = str(uuid.UUID(int=self.counters[category]))
        else:
            replacement = f"{category}{self.counters[category]}"
        self.mapping[value] = replacement
        self._pattern = self._loose = None  # the alternations are stale
        return replacement

    def _contains_known_name(self, token: str) -> bool:
        """Does this identifier embed a name we registered? (`dbo_FactSales`)"""
        if not self.mapping:
            return False
        if self._loose is None:
            body = "|".join(re.escape(name) for name in sorted(self.mapping, key=len, reverse=True))
            self._loose = re.compile(body)
        return bool(self._loose.search(token))

    # -- pass 1: discover every identity ----------------------------------
    def register(self, payload: Any, _key: str = "") -> None:
        """Walk a document and assign a pseudonym to every identity value.

        Identity-bearing keys register their whole value; every other string
        is swept for emails, GUIDs and hostnames, so a server named only
        inside an M expression is still caught.
        """
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.register(value, key)
            return
        if isinstance(payload, list):
            for item in payload:
                self.register(item, _key)
            return
        if not isinstance(payload, str) or not payload:
            return

        lowered = _key.lower()
        if lowered in CONTRACT_KEYS or lowered in DROPPED_KEYS:
            return

        category = IDENTITY_KEYS.get(lowered)
        if category:
            self.pseudonym(payload, category)

        for match in _EMAIL.findall(payload):
            self.pseudonym(match, "User")
        for match in _GUID.findall(payload):
            self.pseudonym(match, "Guid")
        for match in _HOST.finditer(payload):
            token = match.group(0)
            # an email's domain is already covered by the address itself
            if "@" not in payload or token not in payload.split("@", 1)[-1]:
                self.pseudonym(token, "Host")

        # Non-GUID object ids ("ds-finance-sql", "cap-premium-p1") often carry
        # a host or environment name. Pseudonymize them, keeping them unique so
        # cross-references such as datasourceUsages -> datasourceInstances
        # still resolve.
        if lowered.endswith("id") and lowered not in CONTRACT_KEYS:
            self.pseudonym(payload, "Id")

    def register_expressions(self, payload: Any, _key: str = "") -> None:
        """Second discovery pass: names that appear only inside M or DAX.

        Deliberately after `register`, so a value that has a real category —
        a server, a database — is labelled by it rather than becoming a
        generic `Name` because an expression happened to mention it first.
        """
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.register_expressions(value, key)
            return
        if isinstance(payload, list):
            for item in payload:
                self.register_expressions(item, _key)
            return
        if isinstance(payload, str) and _key.lower() in EXPRESSION_KEYS:
            self._register_expression_names(payload)

    def _register_expression_names(self, source: str) -> None:
        """Register every author-chosen name in an M or DAX expression.

        Source columns are the reason this exists: a column that is renamed in
        the query appears only as a string literal, never under a `name` key,
        so without this pass `"SalesAmount"` would be left in the capture.
        """
        for match in _EXPRESSION_TOKEN.finditer(source):
            for group in ("step", "literal", "bracket"):
                value = match.group(group)
                if value is None:
                    continue
                candidate = value.strip()
                if not candidate or candidate.lower() in _M_TYPE_WORDS:
                    continue
                if _EMAIL.fullmatch(candidate) or _GUID.fullmatch(candidate):
                    continue  # already registered under its own category
                if _HOST.fullmatch(candidate):
                    self.pseudonym(candidate, "Host")
                else:
                    self.pseudonym(candidate, "Name")

    # -- pass 2: rewrite ---------------------------------------------------
    def scrub(self, payload: Any, _key: str = "") -> Any:
        if not self.enabled:
            return payload
        if isinstance(payload, dict):
            return {
                key: ("<redacted>" if key.lower() in DROPPED_KEYS else self.scrub(value, key))
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self.scrub(item, _key) for item in payload]
        if not isinstance(payload, str):
            return payload
        lowered = _key.lower()
        if lowered in CONTRACT_KEYS:
            return payload
        if lowered in EXPRESSION_KEYS:
            return self.scrub_expression(payload)
        return self.scrub_text(payload)

    def scrub_text(self, text: str) -> str:
        """Apply the map in one non-overlapping pass, at token boundaries.

        One `re.sub` over an alternation rather than repeated `str.replace`:
        substituted text is never rescanned, so a pseudonym cannot be hit by
        a later, shorter mapping entry. Longest alternatives come first so
        'SalesAmount' wins over 'Sales', and the boundary guards stop a short
        name from being replaced inside a longer identifier — which would
        both corrupt the identifier and leak the rest of it.
        """
        if not self.enabled or not text or not self.mapping:
            return text
        if self._pattern is None:
            alternatives = sorted(self.mapping, key=len, reverse=True)
            body = "|".join(re.escape(name) for name in alternatives)
            self._pattern = re.compile(rf"(?<![A-Za-z0-9_])(?:{body})(?![A-Za-z0-9_])")
        return self._pattern.sub(lambda match: self.mapping[match.group(0)], text)

    def scrub_expression(self, source: str) -> str:
        """Rewrite an M or DAX expression by position rather than by substring.

        Step names, string literals and bracketed identifiers are names the
        author chose, so they are replaced wholesale; bare identifiers are
        replaced only when they are a name we know, which leaves the standard
        library (`Table.SelectColumns`, `SUM`, `each`, `type number`) intact.
        """
        if not self.enabled or not source:
            return source

        def replace(match: re.Match[str]) -> str:
            step, literal = match.group("step"), match.group("literal")
            bracket, ident = match.group("bracket"), match.group("ident")

            if step is not None:
                return f'#"{self._name_for(step)}"'
            if literal is not None:
                if literal.strip().lower() in _M_TYPE_WORDS or not literal.strip():
                    return match.group(0)
                return f'"{self._name_for(literal)}"'
            if bracket is not None:
                return f"[{self._name_for(bracket)}]"

            # A bare identifier next to a dot is a qualified library call
            # (`Table.SelectColumns`), never a name to replace — even if the
            # model happens to contain a table called "Table".
            before = source[match.start() - 1] if match.start() else ""
            after = source[match.end()] if match.end() < len(source) else ""
            if before == "." or after == ".":
                return ident
            known = self.mapping.get(ident)
            if known is not None:
                return known
            # `dbo_FactSales`: a compound step name embedding a real table.
            if self._contains_known_name(ident):
                return self.pseudonym(ident, "Name")
            return ident

        return _EXPRESSION_TOKEN.sub(replace, source)

    def _name_for(self, value: str) -> str:
        """Map a name-bearing token, registering it if pass 1 somehow missed it."""
        known = self.mapping.get(value)
        if known is not None:
            return known
        stripped = value.strip()
        if stripped != value and stripped in self.mapping:
            return self.mapping[stripped]
        return self.pseudonym(value, "Host" if _HOST.fullmatch(value) else "Name")

    def apply(self, payload: Any) -> Any:
        """Discover, then rewrite — the full sequence callers actually want."""
        self.register(payload)
        self.register_expressions(payload)
        return self.scrub(payload)

    def summary(self) -> dict[str, int]:
        return dict(sorted(self.counters.items()))


def capture_bundle(
    client: PowerBIAdminClient,
    settings: Settings,
    workspace_ids: list[str],
    xmla: XmlaClient | None = None,
    include_export_probe: bool = True,
    scrub: bool = True,
    sleep=None,
) -> dict[str, Any]:
    """Collect one redacted bundle describing how this tenant answers.

    Every section is independent: a section that fails records the failure
    and the rest of the bundle is still produced.
    """
    scrubber = Scrubber(enabled=scrub)
    bundle: dict[str, Any] = {
        "pbilineage_version": __version__,
        "scrubbed": scrub,
        "environment": _environment(settings, xmla),
        "sections": {},
    }
    sections = bundle["sections"]
    kwargs = {"sleep": sleep} if sleep is not None else {}

    # -- capacities -------------------------------------------------------
    try:
        skus = client.get_capacity_skus()
        sections["capacities"] = {
            "count": len(skus),
            # SKUs are the routing input; capacity ids are not interesting.
            "skus_observed": sorted({sku for sku in skus.values() if sku}),
        }
    except ApiError as exc:
        sections["capacities"] = {"error": str(exc)}

    # -- raw scan result --------------------------------------------------
    raw_results: list[dict[str, Any]] = []
    try:
        raw_results = client.scan_workspaces(workspace_ids, **kwargs)
        sections["raw_scan"] = scrubber.apply(raw_results)
    except ApiError as exc:
        sections["raw_scan"] = {"error": str(exc)}

    # -- what our normalizer made of it -----------------------------------
    if raw_results:
        try:
            snapshot = snapshot_from_scan_results(raw_results, {})
            sections["normalized"] = scrubber.scrub(_normalized_summary(snapshot))
        except Exception as exc:  # noqa: BLE001 - a bad read must not lose the raw capture
            sections["normalized"] = {"error": f"{type(exc).__name__}: {exc}"}

    # -- DMV shapes -------------------------------------------------------
    sections["dmv"] = _capture_dmv(raw_results, xmla, scrubber)

    # -- export endpoint shapes -------------------------------------------
    if include_export_probe:
        sections["export_probe"] = _capture_export_probe(client, raw_results, scrubber)

    bundle["redactions"] = scrubber.summary()
    return bundle


def _environment(settings: Settings, xmla: XmlaClient | None) -> dict[str, Any]:
    optional = {}
    for module in ("msal", "pyadomd", "neo4j", "sqlglot"):
        try:
            __import__(module)
            optional[module] = "installed"
        except ImportError:
            optional[module] = "absent"
    return {
        "settings": settings.redacted(),
        "optional_dependencies": optional,
        "xmla_available": bool(xmla and xmla.available),
        "xmla_unavailable_reason": xmla.unavailable_reason() if xmla else "no XMLA client",
    }


def _normalized_summary(snapshot) -> dict[str, Any]:
    """A compact view of what we understood, to compare against the raw shape."""
    return {
        "workspaces": [
            {
                "name": workspace.name,
                "tier": workspace.tier.value,
                "capacity_sku": workspace.capacity_sku,
                "datasets": [
                    {
                        "name": dataset.name,
                        "tables": [
                            {
                                "name": table.name,
                                "columns": len(table.columns),
                                "calculated_columns": sum(
                                    1 for column in table.columns if column.is_calculated
                                ),
                                "measures": len(table.measures),
                                "is_calculated": table.is_calculated,
                                "partition_types": [p.source_type for p in table.partitions],
                                # Did we actually get the expression text?
                                "partitions_with_expression": sum(
                                    1 for p in table.partitions if p.expression
                                ),
                                "measures_with_expression": sum(1 for m in table.measures if m.expression),
                            }
                            for table in dataset.tables
                        ],
                        "shared_expressions": len(dataset.expressions),
                        "data_sources": [
                            {"kind": source.kind, "server": source.server} for source in dataset.data_sources
                        ],
                    }
                    for dataset in workspace.datasets
                ],
                "reports": len(workspace.reports),
                "dataflows": len(workspace.dataflows),
            }
            for workspace in snapshot.workspaces
        ],
        "warnings": snapshot.warnings,
    }


#: DMVs worth sampling — the ones the resolvers and the schema reader depend on
DMV_SAMPLES = {
    "DISCOVER_CALC_DEPENDENCY": CALC_DEPENDENCY_QUERY,
    "TMSCHEMA_PARTITIONS": "SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS",
    "TMSCHEMA_COLUMNS": "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS",
}


def _capture_dmv(
    raw_results: list[dict[str, Any]], xmla: XmlaClient | None, scrubber: Scrubber
) -> dict[str, Any]:
    if xmla is None or not xmla.available:
        return {
            "skipped": xmla.unavailable_reason() if xmla else "no XMLA client configured",
        }

    snapshot = snapshot_from_scan_results(raw_results, {})
    xmla.workspace_names.update({w.id: w.name for w in snapshot.workspaces})
    dataset = next((d for w in snapshot.workspaces if w.tier.has_xmla for d in w.datasets), None)
    if dataset is None:
        return {"skipped": "no dataset in a workspace with an XMLA endpoint"}

    captured: dict[str, Any] = {}
    for label, statement in DMV_SAMPLES.items():
        try:
            rows = xmla.query(dataset, statement)
        except XmlaUnavailable as exc:
            captured[label] = {"error": str(exc)}
            continue
        captured[label] = {
            "row_count": len(rows),
            # Column names are the contract; the first rows show the values.
            "columns": sorted({str(key) for row in rows[:50] for key in row}),
            "distinct_object_types": sorted(
                {
                    str(row.get(key))
                    for row in rows
                    for key in row
                    if str(key).upper().endswith("OBJECT_TYPE") and row.get(key)
                }
            ),
            "sample_rows": scrubber.apply([_stringify(row) for row in rows[:5]]),
        }
    return captured


def _stringify(row: dict[str, Any]) -> dict[str, Any]:
    """DMV rows can hold .NET types the JSON encoder will not touch."""
    return {
        str(key): value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        for key, value in row.items()
    }


def _capture_export_probe(
    client: PowerBIAdminClient, raw_results: list[dict[str, Any]], scrubber: Scrubber
) -> dict[str, Any]:
    for result in raw_results:
        for workspace in result.get("workspaces") or []:
            if not isinstance(workspace, dict):
                continue
            for report in workspace.get("reports") or []:
                if isinstance(report, dict) and report.get("id"):
                    probe = client.probe_export(str(workspace.get("id")), str(report["id"]))
                    return scrubber.scrub(probe)
    return {"skipped": "no report found in the scanned workspaces"}
