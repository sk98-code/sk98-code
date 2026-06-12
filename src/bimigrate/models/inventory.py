"""Unified inventory model produced by all four platform parsers.

A single normalized shape lets the complexity scorer, dedup engine,
mapping matcher and report writers treat every estate uniformly while the
`platform_extras` bag preserves source-specific detail losslessly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from bimigrate.models.core import SourceTool


class ComplexityBand(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ParseIssue(BaseModel):
    severity: str = "warning"  # info | warning | error
    location: str = ""
    message: str


class Calculation(BaseModel):
    name: str
    expression: str
    kind: str = "calculated_field"  # calculated_field | table_calc | lod | set_analysis | variable | measure
    datatype: str | None = None
    caption: str | None = None
    dependencies: list[str] = Field(default_factory=list)


class Visual(BaseModel):
    name: str
    visual_type: str  # normalized type, e.g. "bar", "line", "pivot_table"
    raw_type: str = ""  # the platform's own type string
    worksheet: str | None = None
    fields: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    is_custom: bool = False  # custom viz / extension / mod -> MANUAL tier


class Worksheet(BaseModel):
    name: str
    visuals: list[Visual] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)  # filter/highlight/url/navigation actions


class DataConnection(BaseModel):
    name: str
    connection_type: str  # e.g. "sqlserver", "hyper-extract", "information-link", "qvd"
    server: str | None = None
    database: str | None = None
    custom_sql: str | None = None
    is_extract: bool = False


class DataTable(BaseModel):
    name: str
    columns: list[str] = Field(default_factory=list)
    source_connection: str | None = None
    row_count: int | None = None


class ScriptBlock(BaseModel):
    name: str
    language: str  # "qlik-load-script" | "ironpython" | "terr" | "r" | "python" | "vbscript" | "custom-sql"
    code: str
    purpose: str = ""


class SecurityRule(BaseModel):
    kind: str  # "user_filter" | "datasource_filter" | "section_access" | "library_permission" | "data_restriction"
    subject: str = ""  # user/group/field the rule applies to
    definition: str = ""


class ScheduleItem(BaseModel):
    kind: str  # "refresh" | "subscription" | "alert" | "reload_task"
    name: str
    cadence: str | None = None
    target: str | None = None


class ArtifactInventory(BaseModel):
    """Everything we extracted from one source artifact (workbook/dxp/qvw/qvf)."""

    source_tool: SourceTool
    artifact_name: str
    artifact_path: str
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parser_version: str = "1.0"

    worksheets: list[Worksheet] = Field(default_factory=list)
    dashboards: list[str] = Field(default_factory=list)
    stories: list[str] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    connections: list[DataConnection] = Field(default_factory=list)
    tables: list[DataTable] = Field(default_factory=list)
    scripts: list[ScriptBlock] = Field(default_factory=list)
    security: list[SecurityRule] = Field(default_factory=list)
    schedules: list[ScheduleItem] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)

    feature_flags: dict[str, int] = Field(default_factory=dict)  # feature_name -> occurrence count
    platform_extras: dict[str, Any] = Field(default_factory=dict)
    issues: list[ParseIssue] = Field(default_factory=list)

    complexity_score: float = 0.0
    complexity_band: ComplexityBand = ComplexityBand.SIMPLE
    fingerprint: str = ""  # stable content hash used by dedup

    @property
    def visual_count(self) -> int:
        return sum(len(ws.visuals) for ws in self.worksheets)

    def feature_shingles(self) -> list[str]:
        """Tokens fed to MinHash for near-duplicate clustering."""
        shingles: list[str] = []
        for ws in self.worksheets:
            shingles.append(f"ws:{ws.name.lower()}")
            for v in ws.visuals:
                shingles.append(f"viz:{v.visual_type}")
                shingles.extend(f"field:{f.lower()}" for f in v.fields)
        for calc in self.calculations:
            shingles.append(f"calc:{' '.join(calc.expression.lower().split())}")
        for conn in self.connections:
            shingles.append(f"conn:{conn.connection_type}:{(conn.database or '').lower()}")
        for table in self.tables:
            shingles.extend(f"col:{table.name.lower()}.{c.lower()}" for c in table.columns)
        return shingles
