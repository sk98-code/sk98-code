"""pbi_lineage — Power BI metadata, dependency and lineage analyzer.

Milestone 1: file readers. `read_pbix` / `read_pbip` normalize a report +
semantic model from disk into the schema dataclasses; no dependency
resolution happens here.
"""

from pbi_lineage.readers import detect_format, read_any, read_pbip, read_pbix
from pbi_lineage.schema import (
    CalcDependency,
    CalculationGroup,
    CalculationItem,
    Column,
    FieldReference,
    Hierarchy,
    HierarchyLevel,
    LiveConnection,
    Measure,
    Model,
    NamedExpression,
    Page,
    Partition,
    ReadResult,
    RefKind,
    Relationship,
    ReportLayout,
    ReportLevelMeasure,
    Role,
    Table,
    Visual,
)

__all__ = [
    "CalcDependency",
    "CalculationGroup",
    "CalculationItem",
    "Column",
    "FieldReference",
    "Hierarchy",
    "HierarchyLevel",
    "LiveConnection",
    "Measure",
    "Model",
    "NamedExpression",
    "Page",
    "Partition",
    "ReadResult",
    "RefKind",
    "Relationship",
    "ReportLayout",
    "ReportLevelMeasure",
    "Role",
    "Table",
    "Visual",
    "detect_format",
    "read_any",
    "read_pbip",
    "read_pbix",
]

__version__ = "0.1.0"
