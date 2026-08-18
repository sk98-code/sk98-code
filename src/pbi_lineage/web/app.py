"""FastAPI backend for the local pbi_lineage UI.

A thin HTTP layer over the same engines the CLI uses — the analysis core
is untouched. One analysis is held in memory at a time, which is the right
model for a single-user desktop tool launched with `pbi-lineage ui`.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from pbi_lineage.graph import nid_column
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.persist import ScanBundle, write_json, write_sqlite
from pbi_lineage.readers import detect_format, read_any
from pbi_lineage.removal import plan_removal
from pbi_lineage.resolve import (
    STATUS_DYNAMIC,
    STATUS_EXTERNAL,
    STATUS_INCOMPLETE,
    STATUS_REMOVE_MANUALLY,
    STATUS_UNUSED,
    STATUS_USED,
    analyze_model,
)
from pbi_lineage.rules import EstateContext, run_rules

STATIC_DIR = Path(__file__).parent / "static"

STATUS_ORDER = [
    STATUS_USED,
    STATUS_UNUSED,
    STATUS_REMOVE_MANUALLY,
    STATUS_DYNAMIC,
    STATUS_INCOMPLETE,
    STATUS_EXTERNAL,
]


class AnalyzeIn(BaseModel):
    path: str


@dataclass
class Session:
    """Everything produced by the most recent analysis."""

    log: list[dict] = field(default_factory=list)
    source_path: str | None = None
    bundle: ScanBundle | None = None
    m_index: MExpressionIndex | None = None
    duration_s: float | None = None

    def note(self, message: str, level: str = "info") -> None:
        self.log.append({"time": datetime.now().strftime("%H:%M:%S"), "level": level, "message": message})

    def reset(self) -> None:
        self.log = []
        self.source_path = None
        self.bundle = None
        self.m_index = None
        self.duration_s = None


def create_app() -> FastAPI:
    app = FastAPI(title="pbi-lineage", docs_url="/api/docs")
    session = Session()
    app.state.session = session

    # ---------------------------------------------------------------- UI --

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # ----------------------------------------------------------- analysis --

    @app.post("/api/analyze")
    def analyze(payload: AnalyzeIn) -> dict:
        session.reset()
        path = Path(payload.path.strip().strip('"').strip("'")).expanduser()
        session.note(f"File pre-selected: {path}")
        if not path.exists():
            session.note(f"Path does not exist: {path}", "error")
            raise HTTPException(400, f"Path does not exist: {path}")

        detected = detect_format(path)
        if detected is None:
            session.note("Not a recognizable PBIX file or PBIP project", "error")
            raise HTTPException(400, "Not a recognizable PBIX file or PBIP project")

        session.note(f"Started execution ({detected.upper()})")
        started = time.perf_counter()
        try:
            result = read_any(path)
        except Exception as exc:  # noqa: BLE001 — surface parse failures in the log
            session.note(f"Read failed: {exc}", "error")
            traceback.print_exc()
            raise HTTPException(400, f"Could not read {path.name}: {exc}") from exc

        for warning in result.warnings:
            session.note(warning, "warn")

        if result.model is None:
            session.note(
                "No model metadata in this file (thin report, or a legacy ABF DataModel "
                "that needs live Desktop mode). Report layer parsed only.",
                "warn",
            )
            session.source_path = str(path)
            session.duration_s = round(time.perf_counter() - started, 1)
            return {"ok": False, "reason": "no_model", "log": session.log}

        reports = [result.report] if result.report is not None else []
        session.note(f"Added report: {path}")
        session.note(f"Building dependencies for [{result.model.name}]")
        analysis = analyze_model(result.model, reports)

        m_index = MExpressionIndex()
        m_index.add_model(result.model)

        findings = run_rules(
            EstateContext(model=result.model, analysis=analysis, reports=reports, m_index=m_index)
        )
        for disagreement in analysis.disagreements:
            session.note(f"cross-check: {disagreement.object_id}: {disagreement.detail}", "warn")
        for reference in analysis.unresolved[:50]:
            session.note(
                f"Reference skipped: {reference.table}[{reference.name}] is not in the model "
                f"({reference.scope})",
                "warn",
            )

        session.duration_s = round(time.perf_counter() - started, 1)
        session.note(f"Execution duration: {session.duration_s} s")
        session.source_path = str(path)
        session.m_index = m_index
        session.bundle = ScanBundle(
            model=result.model,
            analysis=analysis,
            reports=reports,
            findings=findings,
            coverage={
                "summary": "single local file — external consumers unknown",
                "complete": False,
            },
        )
        return {"ok": True, "log": session.log, "summary": _summary(session)}

    @app.get("/api/state")
    def state() -> dict:
        return {
            "loaded": session.bundle is not None,
            "source": session.source_path,
            "log": session.log,
            "summary": _summary(session) if session.bundle else None,
        }

    # ------------------------------------------------------------ objects --

    @app.get("/api/objects")
    def objects(kind: str = "", status: str = "", q: str = "") -> dict:
        bundle = _require(session)
        rows = []
        needle = q.lower().strip()
        for verdict in bundle.analysis.verdicts.values():
            if kind and verdict.kind != kind:
                continue
            if status and verdict.status != status:
                continue
            label = f"{verdict.table or ''} {verdict.name}".lower()
            if needle and needle not in label:
                continue
            rows.append(
                {
                    "id": verdict.object_id,
                    "name": verdict.name,
                    "table": verdict.table,
                    "kind": verdict.kind,
                    "status": verdict.status,
                    "hidden": verdict.is_hidden,
                    "reasons": verdict.reasons[:4],
                    "evidence": verdict.evidence[:4],
                    "deletable": verdict.deletable,
                }
            )
        rows.sort(key=lambda r: (r["kind"], (r["table"] or ""), r["name"]))
        return {"rows": rows, "total": len(rows)}

    @app.get("/api/tree")
    def tree(node: str, direction: str = "down", depth: int = 6) -> dict:
        bundle = _require(session)
        graph = bundle.analysis.graph
        if node not in graph.nodes:
            raise HTTPException(404, f"unknown object: {node}")
        raw = (
            graph.dependency_tree(node, max_depth=depth)
            if direction == "down"
            else graph.consumers_tree(node, max_depth=depth)
        )
        return {"tree": _tree_json(graph, raw), "direction": direction}

    # ------------------------------------------------------ M expressions --

    @app.get("/api/m-expressions")
    def m_expressions(q: str = "") -> dict:
        _require(session)
        index = session.m_index
        entries = []
        for entry in index.entries:
            if q and q.lower() not in entry.m_code.lower() and q.lower() not in entry.entry_name.lower():
                continue
            entries.append(
                {
                    "name": entry.entry_name,
                    "item": entry.item_name,
                    "kind": entry.kind,
                    "code": entry.m_code,
                    "steps": entry.steps,
                    "sources": [s.label() for s in entry.sources],
                }
            )
        return {"entries": entries}

    @app.get("/api/m-impact")
    def m_impact(object_name: str) -> dict:
        _require(session)
        return {"rows": session.m_index.impact_of(object_name)}

    # ---------------------------------------------------------- findings --

    @app.get("/api/findings")
    def findings() -> dict:
        bundle = _require(session)
        return {
            "rows": [
                {
                    "rule": finding.rule_id,
                    "severity": finding.severity,
                    "object": finding.object_id,
                    "message": finding.message,
                }
                for finding in bundle.findings
            ]
        }

    # ----------------------------------------------------------- removal --

    @app.post("/api/removal-plan")
    def removal_plan(payload: dict) -> dict:
        bundle = _require(session)
        targets = payload.get("objects") or []
        if not targets:
            raise HTTPException(400, "no objects selected")
        plan = plan_removal(bundle.analysis.graph, bundle.analysis.verdicts, targets, model=bundle.model)
        return {
            "blocked": plan.blocked,
            "block_reasons": plan.block_reasons,
            "targets": plan.targets,
            "impact": [
                {"object": item.object_id, "kind": item.kind, "detail": item.detail} for item in plan.impact
            ],
            "tmsl": "" if plan.blocked else plan.tmsl_script,
            "backup": plan.backup_script,
            "manifest": plan.manifest,
            "summary": plan.summary(),
        }

    # ------------------------------------------------------------ export --

    @app.get("/api/export")
    def export(fmt: str = "json") -> FileResponse:
        bundle = _require(session)
        out_dir = Path(session.source_path or ".").parent / "pbi_lineage_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        if fmt == "sqlite":
            path = write_sqlite(out_dir / "pbi_lineage.sqlite", [bundle])
            media = "application/x-sqlite3"
        else:
            path = write_json(out_dir / "pbi_lineage_export.json", [bundle])
            media = "application/json"
        return FileResponse(path, filename=path.name, media_type=media)

    return app


# --------------------------------------------------------------- helpers --


def _require(session: Session) -> ScanBundle:
    if session.bundle is None:
        raise HTTPException(400, "nothing analyzed yet")
    return session.bundle


def _summary(session: Session) -> dict:
    bundle = session.bundle
    model = bundle.model
    verdicts = bundle.analysis.verdicts

    def count(kind: str, status: str | None = None) -> int:
        return sum(1 for v in verdicts.values() if v.kind == kind and (status is None or v.status == status))

    by_status = {}
    for status in STATUS_ORDER:
        by_status[status] = sum(1 for v in verdicts.values() if v.status == status)

    total_columns = count("column")
    unused_columns = count("column", STATUS_UNUSED)
    relationships = model.relationships
    severities = {}
    for finding in bundle.findings:
        severities[finding.severity] = severities.get(finding.severity, 0) + 1

    return {
        "model": model.name,
        "source": session.source_path,
        "duration_s": session.duration_s,
        "tables": len(model.tables),
        "columns": {"total": total_columns, "unused": unused_columns},
        "measures": {"total": count("measure"), "unused": count("measure", STATUS_UNUSED)},
        "calc_columns": {
            "total": sum(1 for t in model.tables for c in t.columns if c.is_calculated),
            "unused": sum(
                1
                for t in model.tables
                for c in t.columns
                if c.is_calculated
                and verdicts.get(nid_column(t.name, c.name))
                and verdicts[nid_column(t.name, c.name)].status == STATUS_UNUSED
            ),
        },
        "hierarchies": {"total": count("hierarchy"), "unused": count("hierarchy", STATUS_UNUSED)},
        "relationships": {
            "total": len(relationships),
            "many_to_many": sum(1 for r in relationships if (r.to_cardinality or "") == "many"),
            "inactive": sum(1 for r in relationships if not r.is_active),
            "bidirectional": sum(
                1 for r in relationships if (r.cross_filtering or "").lower() == "bothdirections"
            ),
        },
        "by_status": by_status,
        "unused_pct": round(100 * unused_columns / total_columns) if total_columns else 0,
        "findings": {"total": len(bundle.findings), **severities},
        "unresolved": len(bundle.analysis.unresolved),
        "coverage": bundle.coverage,
    }


def _tree_json(graph, raw: dict, seen: set | None = None) -> dict:
    """Shape one walk for the UI, collapsing repeats.

    The same consumer legitimately reaches an object by several paths (a
    projection *and* a sort *and* a dataTransform). Rendering one row per
    path buries the structure, so identical children are merged and the
    extra evidence is kept as a count the UI can show on demand.
    """
    seen = seen if seen is not None else set()
    node = graph.nodes.get(raw["id"])
    edge = raw.get("edge")
    label = node.name if node else raw["id"]
    if node is not None and node.kind == "visual":
        page = graph.nodes.get(node.parent) if node.parent else None
        suffix = raw["id"].rsplit("/", 1)[-1]
        label = f"{label} ({suffix})" if suffix and suffix not in label else label
        if page is not None:
            label = f"{label} — {page.name}"
    out = {
        "id": raw["id"],
        "label": label,
        "kind": node.kind if node else "?",
        "edge_kind": edge.kind if edge else None,
        "confidence": edge.confidence if edge else None,
        "evidence": edge.evidence if edge else None,
        "extra_evidence": [],
        "children": [],
    }
    if raw["id"] in seen:
        out["cycle"] = True
        return out
    seen = seen | {raw["id"]}

    merged: dict[str, dict] = {}
    for child in raw.get("children", []):
        rendered = _tree_json(graph, child, seen)
        existing = merged.get(rendered["id"])
        if existing is None:
            merged[rendered["id"]] = rendered
            continue
        if rendered["evidence"] and rendered["evidence"] != existing["evidence"]:
            existing["extra_evidence"].append(rendered["evidence"])
        for grandchild in rendered["children"]:
            if all(g["id"] != grandchild["id"] for g in existing["children"]):
                existing["children"].append(grandchild)
    out["children"] = list(merged.values())
    return out
