"""VertiPaq size attribution (build spec §4.5, Milestone 5 — capability C3).

Total column cost = data segments + dictionary + attribute-hierarchy
structures + a share of the user hierarchies built on it — the VertiPaq
Analyzer method, read from the storage DMVs:

    DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS   USED_SIZE per segment
    DISCOVER_STORAGE_TABLE_COLUMNS           DICTIONARY_SIZE
    DISCOVER_STORAGE_TABLES                  row counts

Segment TABLE_IDs carry structural prefixes: `H$` attribute hierarchy,
`U$` user hierarchy, `R$` relationship index. Object ids look like
`Sales (142)` / `Qty (7)` — the trailing ` (n)` is stripped for matching.

Two spec rules encoded here:

- savings are a **range**, not a number — removing a column removes its
  dictionary but may not shrink compressed neighbours predictably;
- measures cost ~zero storage: this module never attributes bytes to a
  measure, so no caller can present "MB saved" for one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pbi_lineage.connectors.dmv import DmvExecutor, _get, _query

_ID_SUFFIX = re.compile(r"\s*\(\d+\)$")


def _strip_id(raw: str | None) -> str:
    return _ID_SUFFIX.sub("", raw or "").strip()


def _split_table_id(raw: str | None) -> tuple[str, str]:
    """`H$Sales (142)` → ("H$", "Sales"); `Sales (142)` → ("", "Sales")."""
    name = _strip_id(raw)
    for prefix in ("H$", "U$", "R$"):
        if name.startswith(prefix):
            return prefix, name[len(prefix) :]
    return "", name


@dataclass
class ColumnSize:
    table: str
    column: str
    data_bytes: int = 0
    dictionary_bytes: int = 0
    attribute_hierarchy_bytes: int = 0
    user_hierarchy_share_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return (
            self.data_bytes
            + self.dictionary_bytes
            + self.attribute_hierarchy_bytes
            + self.user_hierarchy_share_bytes
        )

    @property
    def reclaimable_low(self) -> int:
        """Certain floor: the column's own data segments + dictionary."""
        return self.data_bytes + self.dictionary_bytes

    @property
    def reclaimable_high(self) -> int:
        """Upper bound including hierarchy structures that go with it."""
        return self.total_bytes


@dataclass
class TableSize:
    table: str
    row_count: int | None = None
    columns: dict[str, ColumnSize] = field(default_factory=dict)
    relationship_bytes: int = 0
    user_hierarchy_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return (
            sum(c.total_bytes for c in self.columns.values())
            + self.relationship_bytes
            + self.user_hierarchy_bytes
        )


@dataclass
class SizeReport:
    tables: dict[str, TableSize] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def column(self, table: str, column: str) -> ColumnSize | None:
        table_size = self.tables.get(table)
        return table_size.columns.get(column) if table_size else None

    def _table(self, name: str) -> TableSize:
        if name not in self.tables:
            self.tables[name] = TableSize(table=name)
        return self.tables[name]

    def _column(self, table: str, column: str) -> ColumnSize:
        table_size = self._table(table)
        if column not in table_size.columns:
            table_size.columns[column] = ColumnSize(table=table, column=column)
        return table_size.columns[column]


def read_storage_sizes(executor: DmvExecutor, *, warnings: list[str] | None = None) -> SizeReport:
    warnings = warnings if warnings is not None else []
    report = SizeReport(warnings=warnings)

    for row in _query(executor, "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS", warnings):
        prefix, table = _split_table_id(_get(row, "TABLE_ID"))
        column = _strip_id(_get(row, "COLUMN_ID"))
        used = int(_get(row, "USED_SIZE", default=0) or 0)
        if prefix == "":
            report._column(table, column).data_bytes += used
        elif prefix == "H$":
            report._column(table, column).attribute_hierarchy_bytes += used
        elif prefix == "U$":
            report._table(table).user_hierarchy_bytes += used
        elif prefix == "R$":
            report._table(table).relationship_bytes += used

    for row in _query(executor, "DISCOVER_STORAGE_TABLE_COLUMNS", warnings):
        prefix, table = _split_table_id(_get(row, "TABLE_ID"))
        if prefix:
            continue  # hierarchy/relationship rows carry no dictionaries
        column = _strip_id(_get(row, "COLUMN_ID"))
        dictionary = int(_get(row, "DICTIONARY_SIZE", default=0) or 0)
        report._column(table, column).dictionary_bytes += dictionary

    for row in _query(executor, "DISCOVER_STORAGE_TABLES", warnings):
        prefix, table = _split_table_id(_get(row, "TABLE_ID"))
        if prefix:
            continue
        rows_count = _get(row, "ROWS_COUNT")
        if rows_count is not None:
            table_size = report._table(table)
            table_size.row_count = max(table_size.row_count or 0, int(rows_count))

    _attribute_user_hierarchies(report)
    return report


def _attribute_user_hierarchies(report: SizeReport) -> None:
    """Spread each table's user-hierarchy bytes across its columns
    proportionally to their own size — the 'user hierarchy share' term.
    The table-level number stays intact; the share is advisory."""
    for table_size in report.tables.values():
        if not table_size.user_hierarchy_bytes or not table_size.columns:
            continue
        own_total = sum(c.data_bytes + c.dictionary_bytes for c in table_size.columns.values())
        if own_total <= 0:
            continue
        for column in table_size.columns.values():
            weight = (column.data_bytes + column.dictionary_bytes) / own_total
            column.user_hierarchy_share_bytes = int(table_size.user_hierarchy_bytes * weight)


def reclaimable_range(report: SizeReport, targets: list[tuple[str, str]]) -> tuple[int, int]:
    """(low, high) reclaimable bytes for a set of (table, column) targets.
    Columns without storage rows contribute zero — never invent bytes."""
    low = high = 0
    for table, column in targets:
        size = report.column(table, column)
        if size is not None:
            low += size.reclaimable_low
            high += size.reclaimable_high
    return low, high
