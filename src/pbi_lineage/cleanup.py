"""Cleanup & actions: TMDL export, DAX backup/restore, documentation,
duplicate-DAX detection, and save/resume of a whole analysis.

These are the operations a developer runs *around* a cleanup: take a
backup of every expression before touching anything, write out a TMDL
copy of the model with the unused objects already removed, hand a
reviewer readable documentation, and be able to put an analysis down and
pick it up tomorrow.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pbi_lineage.graph import nid_column, nid_measure
from pbi_lineage.resolve import STATUS_UNUSED, AnalysisResult
from pbi_lineage.schema import Model

# ---------------------------------------------------------------------------
# Clean TMDL export
# ---------------------------------------------------------------------------


def _q(name: str) -> str:
    """TMDL-quote a name when it needs it."""
    if name and name.replace("_", "").isalnum() and not name[0].isdigit():
        return name
    return "'" + name.replace("'", "''") + "'"


def _indent_block(text: str, level: int) -> list[str]:
    pad = "\t" * level
    return [pad + line for line in text.splitlines() if line.strip()]


def clean_tmdl(
    model: Model, analysis: AnalysisResult | None = None, *, drop_unused: bool = True
) -> dict[str, str]:
    """Render the model as TMDL, optionally with unused objects removed.

    Returns `{relative path: file text}` so the caller can write a folder
    or zip it. Only objects whose verdict is exactly `Unused` are dropped —
    the same rule the removal planner enforces, so a "clean" export can
    never quietly delete something indeterminate.
    """
    removed: set[str] = set()
    if drop_unused and analysis is not None:
        removed = {v.object_id for v in analysis.by_status(STATUS_UNUSED)}

    files: dict[str, str] = {}
    files["definition/database.tmdl"] = f"database {_q(model.name)}\n\tcompatibilityLevel: 1604\n"

    model_lines = ["model Model", f"\tculture: {model.culture or 'en-US'}", ""]
    kept_tables = []
    for table in model.tables:
        if nid_table_id(table.name) in removed:
            continue
        kept_tables.append(table)
        model_lines.append(f"ref table {_q(table.name)}")
    for role in model.roles:
        model_lines.append(f"ref role {_q(role.name)}")
    files["definition/model.tmdl"] = "\n".join(model_lines) + "\n"

    for table in kept_tables:
        lines = [f"table {_q(table.name)}"]
        if table.data_category:
            lines.append(f"\tdataCategory: {table.data_category}")
        if table.is_hidden:
            lines.append("\tisHidden")

        for measure in table.measures:
            if nid_measure(measure.name) in removed:
                continue
            if "\n" in measure.expression:
                lines.append(f"\tmeasure {_q(measure.name)} =")
                lines.extend(_indent_block(measure.expression, 3))
            else:
                lines.append(f"\tmeasure {_q(measure.name)} = {measure.expression}")
            if measure.format_string:
                lines.append(f"\t\tformatString: {measure.format_string}")
            if measure.display_folder:
                lines.append(f"\t\tdisplayFolder: {measure.display_folder}")
            if measure.is_hidden:
                lines.append("\t\tisHidden")
            lines.append("")

        for column in table.columns:
            if nid_column(table.name, column.name) in removed:
                continue
            if column.expression:
                lines.append(f"\tcolumn {_q(column.name)} = {column.expression}")
            else:
                lines.append(f"\tcolumn {_q(column.name)}")
            if column.data_type:
                lines.append(f"\t\tdataType: {column.data_type}")
            if column.is_hidden:
                lines.append("\t\tisHidden")
            if column.is_key:
                lines.append("\t\tisKey")
            if column.summarize_by:
                lines.append(f"\t\tsummarizeBy: {column.summarize_by}")
            if column.sort_by_column and nid_column(table.name, column.sort_by_column) not in removed:
                lines.append(f"\t\tsortByColumn: {_q(column.sort_by_column)}")
            if column.source_column and not column.expression:
                lines.append(f"\t\tsourceColumn: {column.source_column}")
            lines.append("")

        for hierarchy in table.hierarchies:
            if nid_hierarchy_id(table.name, hierarchy.name) in removed:
                continue
            lines.append(f"\thierarchy {_q(hierarchy.name)}")
            for level in hierarchy.levels:
                lines.append(f"\t\tlevel {_q(level.name)}")
                if level.column:
                    lines.append(f"\t\t\tcolumn: {_q(level.column)}")
            lines.append("")

        for partition in table.partitions:
            if not partition.expression:
                continue
            lines.append(f"\tpartition {_q(partition.name)} = {partition.source_type or 'm'}")
            if partition.mode:
                lines.append(f"\t\tmode: {partition.mode}")
            lines.append("\t\tsource =")
            lines.extend(_indent_block(partition.expression, 3))
            lines.append("")

        files[f"definition/tables/{_safe(table.name)}.tmdl"] = "\n".join(lines).rstrip("\n") + "\n"

    if model.relationships:
        lines = []
        for rel in model.relationships:
            lines.append(f"relationship {_q(rel.name)}")
            if not rel.is_active:
                lines.append("\tisActive: false")
            if rel.to_cardinality:
                lines.append(f"\ttoCardinality: {rel.to_cardinality}")
            if rel.cross_filtering:
                lines.append(f"\tcrossFilteringBehavior: {rel.cross_filtering}")
            lines.append(f"\tfromColumn: {_q(rel.from_table)}.{_q(rel.from_column)}")
            lines.append(f"\ttoColumn: {_q(rel.to_table)}.{_q(rel.to_column)}")
            lines.append("")
        files["definition/relationships.tmdl"] = "\n".join(lines).rstrip("\n") + "\n"

    for role in model.roles:
        lines = [f"role {_q(role.name)}", f"\tmodelPermission: {role.model_permission or 'read'}"]
        for table_name, expression in role.table_permissions.items():
            lines.append(f"\ttablePermission {_q(table_name)} = {expression}")
        files[f"definition/roles/{_safe(role.name)}.tmdl"] = "\n".join(lines) + "\n"

    for expression in model.expressions:
        files.setdefault("definition/expressions.tmdl", "")
        files["definition/expressions.tmdl"] += (
            f"expression {_q(expression.name)} =\n"
            + "\n".join(_indent_block(expression.expression, 1))
            + "\n"
        )
    return files


def nid_table_id(name: str) -> str:
    return f"table:{name}"


def nid_hierarchy_id(table: str, name: str) -> str:
    return f"hierarchy:{table}/{name}"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "_", name).strip() or "unnamed"


def write_clean_tmdl(out_dir: str | Path, files: dict[str, str]) -> Path:
    out_dir = Path(out_dir)
    for relative, text in files.items():
        path = out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return out_dir


# ---------------------------------------------------------------------------
# DAX backup & restore
# ---------------------------------------------------------------------------


@dataclass
class DaxBackup:
    """Every measure and calculated-column expression in the model."""

    model: str
    created_at: str
    measures: list[dict] = field(default_factory=list)
    calculated_columns: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "kind": "pbi-lineage-dax-backup",
            "version": 1,
            "model": self.model,
            "created_at": self.created_at,
            "measures": self.measures,
            "calculated_columns": self.calculated_columns,
        }


def backup_dax(model: Model) -> DaxBackup:
    backup = DaxBackup(model=model.name, created_at=datetime.now(timezone.utc).isoformat())
    for table in model.tables:
        for measure in table.measures:
            backup.measures.append(
                {
                    "table": table.name,
                    "name": measure.name,
                    "expression": measure.expression,
                    "format_string": measure.format_string,
                    "display_folder": measure.display_folder,
                    "is_hidden": measure.is_hidden,
                }
            )
        for column in table.columns:
            if column.expression:
                backup.calculated_columns.append(
                    {
                        "table": table.name,
                        "name": column.name,
                        "expression": column.expression,
                        "data_type": column.data_type,
                    }
                )
    return backup


def restore_dax(model: Model, backup: dict) -> list[str]:
    """Put backed-up expressions back onto a model. Returns what changed,
    so a restore is never silent."""
    changes: list[str] = []
    for row in backup.get("measures", []):
        table = model.table(row.get("table", ""))
        if table is None:
            changes.append(f"skipped measure [{row.get('name')}]: table {row.get('table')} is gone")
            continue
        measure = next((m for m in table.measures if m.name == row.get("name")), None)
        if measure is None:
            from pbi_lineage.schema import Measure  # noqa: PLC0415 - local to keep import graph flat

            table.measures.append(
                Measure(
                    name=row.get("name", ""),
                    expression=row.get("expression", ""),
                    format_string=row.get("format_string"),
                    display_folder=row.get("display_folder"),
                    is_hidden=bool(row.get("is_hidden")),
                )
            )
            changes.append(f"restored missing measure [{row.get('name')}]")
        elif measure.expression != row.get("expression"):
            measure.expression = row.get("expression", "")
            changes.append(f"reverted expression of [{row.get('name')}]")
    for row in backup.get("calculated_columns", []):
        table = model.table(row.get("table", ""))
        if table is None:
            continue
        column = next((c for c in table.columns if c.name == row.get("name")), None)
        if column is not None and column.expression != row.get("expression"):
            column.expression = row.get("expression")
            changes.append(f"reverted expression of {row.get('table')}[{row.get('name')}]")
    return changes


# ---------------------------------------------------------------------------
# Duplicate DAX
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"\s*([(),\[\]{}+\-*/&=<>])\s*")
_WS = re.compile(r"\s+")


def normalize_dax(expression: str) -> str:
    """A comparison form that ignores formatting but never string content.

    Comments and layout are dropped and spacing around operators is
    normalized, so `SUM(Sales[Qty])` and `SUM( Sales[Qty] ) -- note` match.
    String literals are passed through untouched: two measures that differ
    only inside a literal are genuinely different, and a `--` inside a
    string is not a comment.
    """
    text = expression or ""
    out: list[str] = []
    buffer: list[str] = []
    i, length = 0, len(text)

    def flush() -> None:
        chunk = "".join(buffer)
        buffer.clear()
        if not chunk:
            return
        chunk = _WS.sub(" ", chunk)
        chunk = _PUNCT.sub(r"\1", chunk)
        out.append(chunk.lower())

    while i < length:
        char = text[i]
        if char == '"':
            flush()
            start = i
            i += 1
            while i < length:
                if text[i] == '"':
                    if i + 1 < length and text[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(text[start:i])
            continue
        if text.startswith("--", i) or text.startswith("//", i):
            flush()
            end = text.find("\n", i)
            i = length if end < 0 else end + 1
            buffer.append(" ")
            continue
        if text.startswith("/*", i):
            flush()
            end = text.find("*/", i + 2)
            i = length if end < 0 else end + 2
            buffer.append(" ")
            continue
        buffer.append(char)
        i += 1
    flush()
    return "".join(out).strip()


def duplicate_dax(model: Model) -> list[dict]:
    """Measures (and calculated columns) whose DAX is identical once
    comments and whitespace are ignored — the 'same logic, five names'
    problem."""
    buckets: dict[str, list[dict]] = {}
    for table in model.tables:
        for measure in table.measures:
            if not measure.expression.strip():
                continue
            key = normalize_dax(measure.expression)
            buckets.setdefault(key, []).append({"kind": "measure", "table": table.name, "name": measure.name})
        for column in table.columns:
            if column.expression:
                key = normalize_dax(column.expression)
                buckets.setdefault(key, []).append(
                    {"kind": "calculated column", "table": table.name, "name": column.name}
                )
    groups = []
    for key, members in buckets.items():
        if len(members) < 2:
            continue
        groups.append(
            {
                "fingerprint": hashlib.sha1(key.encode()).hexdigest()[:12],
                "expression": key,
                "count": len(members),
                "objects": members,
            }
        )
    return sorted(groups, key=lambda g: -g["count"])


# ---------------------------------------------------------------------------
# Documentation export
# ---------------------------------------------------------------------------


def documentation_markdown(model: Model, analysis: AnalysisResult | None, reports: list) -> str:
    """Readable model + report documentation for a reviewer or a wiki."""
    out: list[str] = [f"# {model.name}", ""]
    if analysis is not None:
        counts: dict[str, int] = {}
        for verdict in analysis.verdicts.values():
            counts[verdict.status] = counts.get(verdict.status, 0) + 1
        out.append("## Usage summary")
        out.append("")
        out.append("| Status | Objects |")
        out.append("|---|---:|")
        for status, count in sorted(counts.items()):
            out.append(f"| {status} | {count} |")
        out.append("")

    out.append("## Tables")
    out.append("")
    for table in model.tables:
        out.append(f"### {table.name}")
        out.append("")
        if table.columns:
            out.append("| Column | Type | Hidden | Calculated | Status |")
            out.append("|---|---|---|---|---|")
            for column in table.columns:
                verdict = analysis.verdicts.get(nid_column(table.name, column.name)) if analysis else None
                out.append(
                    f"| {column.name} | {column.data_type or ''} | "
                    f"{'yes' if column.is_hidden else ''} | "
                    f"{'yes' if column.is_calculated else ''} | {verdict.status if verdict else ''} |"
                )
            out.append("")
        if table.measures:
            out.append("| Measure | Expression | Status |")
            out.append("|---|---|---|")
            for measure in table.measures:
                verdict = analysis.verdicts.get(nid_measure(measure.name)) if analysis else None
                expression = (measure.expression or "").replace("\n", " ").replace("|", "\\|")
                out.append(f"| {measure.name} | `{expression[:160]}` | {verdict.status if verdict else ''} |")
            out.append("")

    if model.relationships:
        out.append("## Relationships")
        out.append("")
        out.append("| From | To | Cardinality | Cross-filter | Active |")
        out.append("|---|---|---|---|---|")
        for rel in model.relationships:
            out.append(
                f"| {rel.from_table}[{rel.from_column}] | {rel.to_table}[{rel.to_column}] | "
                f"{rel.to_cardinality or 'manyToOne'} | {rel.cross_filtering or 'single'} | "
                f"{'yes' if rel.is_active else 'no'} |"
            )
        out.append("")

    for report in reports or []:
        out.append(f"## Report: {report.name}")
        out.append("")
        for page in report.pages:
            out.append(
                f"### Page: {page.display_name or page.name}" + (" (hidden)" if page.is_hidden else "")
            )
            out.append("")
            out.append("| Visual | Type | Fields |")
            out.append("|---|---|---|")
            for visual in page.visuals:
                fields = ", ".join(sorted({f"{t or '?'}[{n}]" for t, n, _ in visual.fields()}))
                out.append(f"| {(visual.name or '')[:12]} | {visual.visual_type or ''} | {fields} |")
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Save & resume an analysis
# ---------------------------------------------------------------------------

SESSION_SUFFIX = ".pbilineage.json"


def save_session(path: str | Path, bundle, *, source_path: str | None, log: list[dict]) -> Path:
    """Persist a whole analysis so it can be reopened without re-running."""
    from pbi_lineage.persist import to_dict  # noqa: PLC0415 - avoids an import cycle

    path = Path(path)
    payload = {
        "kind": "pbi-lineage-session",
        "version": 1,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_path": source_path,
        "log": log,
        "bundle": to_dict(bundle),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_session(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("kind") != "pbi-lineage-session":
        raise ValueError(f"{path} is not a pbi-lineage session file")
    if payload.get("version", 0) > 1:
        raise ValueError("session file is newer than this tool supports")
    return payload
