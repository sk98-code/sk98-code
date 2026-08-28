"""XMLA endpoint access for workspaces on Premium / PPU / Fabric capacity.

Two things come back over XMLA that no REST API gives us:

* `$SYSTEM.DISCOVER_CALC_DEPENDENCY` — fully resolved DAX dependencies,
  which is what makes the Premium path high-confidence.
* `$SYSTEM.TMSCHEMA_*` — the authoritative model schema, including each
  partition's `QueryDefinition` (the M text) and its partition type.

The connection itself needs ADOMD.NET via `pyadomd`, which is not available
everywhere (it needs the .NET runtime). That is treated as a normal, expected
condition: `XmlaClient.available` is False, the router falls back to the
Scanner-API path, and the run continues with heuristic confidence instead of
failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pbilineage.config import Settings, xmla_connection_string
from pbilineage.models import (
    ColumnSpec,
    DatasetSpec,
    MeasureSpec,
    PartitionSpec,
    TableSpec,
)

__all__ = ["XmlaClient", "XmlaUnavailable"]

# $SYSTEM.TMSCHEMA_COLUMNS.Type
COLUMN_TYPES = {1: "data", 2: "calculated", 3: "row_number", 4: "calculated_table_column"}
# $SYSTEM.TMSCHEMA_PARTITIONS.Type
PARTITION_TYPES = {1: "query", 2: "calculated", 3: "none", 4: "m", 5: "entity", 6: "m", 7: "entity"}

TABLES_QUERY = "SELECT [ID], [Name] FROM $SYSTEM.TMSCHEMA_TABLES"
COLUMNS_QUERY = (
    "SELECT [TableID], [ExplicitName], [InferredName], [ExplicitDataType], [Type], "
    "[Expression], [IsHidden], [Description] FROM $SYSTEM.TMSCHEMA_COLUMNS"
)
MEASURES_QUERY = (
    "SELECT [TableID], [Name], [Expression], [IsHidden], [Description] " "FROM $SYSTEM.TMSCHEMA_MEASURES"
)
PARTITIONS_QUERY = (
    "SELECT [TableID], [Name], [QueryDefinition], [Type], [Mode] FROM $SYSTEM.TMSCHEMA_PARTITIONS"
)
EXPRESSIONS_QUERY = "SELECT [Name], [Expression] FROM $SYSTEM.TMSCHEMA_EXPRESSIONS"


class XmlaUnavailable(RuntimeError):
    """Raised when no XMLA endpoint can be reached for a dataset."""


def _cell(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return default


@dataclass(slots=True)
class XmlaClient:
    """Runs DMV queries against a workspace's XMLA endpoint via ADOMD.NET."""

    settings: Settings
    #: workspace id -> workspace name, needed to build the connect string
    workspace_names: dict[str, str] = field(default_factory=dict)
    #: set False to make every call fail fast (used when XMLA is turned off)
    enabled: bool = True

    def __post_init__(self) -> None:
        self._connector = _load_connector()

    @property
    def available(self) -> bool:
        return self.enabled and self.settings.enable_xmla and self._connector is not None

    def unavailable_reason(self) -> str:
        if not self.settings.enable_xmla or not self.enabled:
            return "XMLA collection is disabled by configuration"
        if self._connector is None:
            return (
                "the 'pyadomd' package (and the ADOMD.NET client libraries) is not installed; "
                "install pbilineage[xmla] on a host with the .NET runtime"
            )
        return ""

    def connection_string(self, dataset: DatasetSpec) -> str:
        workspace_name = self.workspace_names.get(dataset.workspace_id, "")
        if not workspace_name:
            raise XmlaUnavailable(
                f"no workspace name known for workspace {dataset.workspace_id}; "
                "XMLA connect strings address workspaces by name"
            )
        return xmla_connection_string(self.settings, workspace_name, dataset.name)

    def query(self, dataset: DatasetSpec, statement: str) -> list[dict[str, Any]]:
        """Execute one DMV statement, returning rows as dicts."""
        if not self.available:
            raise XmlaUnavailable(self.unavailable_reason())
        connect = self.connection_string(dataset)
        assert self._connector is not None
        try:
            return self._connector(connect, statement)
        except Exception as exc:  # noqa: BLE001 - surfaced as a fallback reason
            raise XmlaUnavailable(f"XMLA query failed: {exc}") from exc

    def fetch_schema(self, dataset: DatasetSpec) -> DatasetSpec:
        """Rebuild a dataset's schema from the TMSCHEMA DMVs.

        The Scanner API's schema is good but lossy — notably it does not tell
        us a partition's type. Where XMLA is reachable, this is authoritative.
        """
        tables_by_id: dict[str, TableSpec] = {}
        for row in self.query(dataset, TABLES_QUERY):
            table_id = str(_cell(row, "ID"))
            tables_by_id[table_id] = TableSpec(name=str(_cell(row, "Name")))

        for row in self.query(dataset, COLUMNS_QUERY):
            table = tables_by_id.get(str(_cell(row, "TableID")))
            if table is None:
                continue
            column_type = COLUMN_TYPES.get(int(_cell(row, "Type", default=1) or 1), "data")
            if column_type == "row_number":
                continue
            table.columns.append(
                ColumnSpec(
                    name=str(_cell(row, "ExplicitName", "InferredName")),
                    data_type=str(_cell(row, "ExplicitDataType")),
                    is_calculated=column_type == "calculated",
                    expression=str(_cell(row, "Expression")),
                    is_hidden=bool(_cell(row, "IsHidden", default=False)),
                    description=str(_cell(row, "Description")),
                )
            )

        for row in self.query(dataset, MEASURES_QUERY):
            table = tables_by_id.get(str(_cell(row, "TableID")))
            if table is None:
                continue
            table.measures.append(
                MeasureSpec(
                    name=str(_cell(row, "Name")),
                    table=table.name,
                    expression=str(_cell(row, "Expression")),
                    is_hidden=bool(_cell(row, "IsHidden", default=False)),
                    description=str(_cell(row, "Description")),
                )
            )

        for row in self.query(dataset, PARTITIONS_QUERY):
            table = tables_by_id.get(str(_cell(row, "TableID")))
            if table is None:
                continue
            partition_type = PARTITION_TYPES.get(int(_cell(row, "Type", default=0) or 0), "unknown")
            if partition_type == "calculated":
                table.is_calculated = True
            table.partitions.append(
                PartitionSpec(
                    name=str(_cell(row, "Name")),
                    source_type=partition_type if partition_type != "query" else "m",
                    expression=str(_cell(row, "QueryDefinition")),
                )
            )

        expressions: dict[str, str] = {}
        try:
            for row in self.query(dataset, EXPRESSIONS_QUERY):
                expressions[str(_cell(row, "Name"))] = str(_cell(row, "Expression"))
        except XmlaUnavailable:
            pass  # TMSCHEMA_EXPRESSIONS is absent on older compat levels

        return dataset.model_copy(
            update={
                "tables": list(tables_by_id.values()),
                "expressions": expressions or dataset.expressions,
            }
        )


def _load_connector():
    """Return a callable (connect_string, query) -> rows, or None if absent."""
    try:
        from pyadomd import Pyadomd  # type: ignore[import-not-found]
    except Exception:  # ImportError, or a .NET runtime that will not load
        return None

    def run(connect_string: str, statement: str) -> list[dict[str, Any]]:
        with Pyadomd(connect_string) as connection:
            with connection.cursor().execute(statement) as cursor:
                names = [description[0] for description in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]

    return run
