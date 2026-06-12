"""FastAPI backend for the interactive web app.

Thin HTTP layer over the same engines the CLI uses — no logic lives here.
A server-side workspace directory holds uploaded estate files and emitted
PBIP output; everything else is computed per request from the shared
KnowledgeBase / MappingRepository singletons.

Run with: bimigrate web  (requires the [web] extra)
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from bimigrate.convert import ConversionEngine, LoadScriptConverter
from bimigrate.convert.ironpython import classify_ironpython
from bimigrate.engine.complexity import effort_hours, score_inventory
from bimigrate.engine.dedup import cluster_inventories, dedup_savings_estimate
from bimigrate.engine.scrub import scrub_inventory
from bimigrate.kb import KnowledgeBase
from bimigrate.mapping import MappingRepository
from bimigrate.models.core import tier_for_confidence
from bimigrate.parsers.base import ParserError, get_parser_for, registered_extensions

STATIC_DIR = Path(__file__).parent / "static"


# request models live at module level: `from __future__ import annotations`
# stringifies hints, and FastAPI can only resolve them from module globals
class ExpressionIn(BaseModel):
    tool: str
    expression: str
    table_hint: str = "Data"


class ScriptIn(BaseModel):
    script: str


class IronPythonIn(BaseModel):
    name: str = "script"
    code: str


class AppState:
    def __init__(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="bimigrate-web-"))
        (self.workspace / "uploads").mkdir()
        (self.workspace / "output").mkdir()
        self.kb = KnowledgeBase()
        self.kb.init_from_seeds()
        self.mappings = MappingRepository()
        self.mappings.init_from_seeds()
        self.engine = ConversionEngine(self.kb)
        self.inventories: list = []


def create_app() -> FastAPI:
    app = FastAPI(title="bimigrate", docs_url="/api/docs")
    state = AppState()
    app.state.bimigrate = state

    # -- frontend ---------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # -- estate -------------------------------------------------------------

    @app.get("/api/info")
    def info() -> dict:
        return {
            "extensions": registered_extensions(),
            "kb": state.kb.stats(),
            "mappings": len(state.mappings.all()),
            "uploads": sorted(p.name for p in (state.workspace / "uploads").iterdir()),
        }

    @app.post("/api/upload")
    async def upload(files: list[UploadFile]) -> dict:
        saved, rejected = [], []
        exts = set(registered_extensions())
        for file in files:
            name = Path(file.filename or "upload").name
            if Path(name).suffix.lower() not in exts:
                rejected.append(name)
                continue
            dest = state.workspace / "uploads" / name
            dest.write_bytes(await file.read())
            saved.append(name)
        return {"saved": saved, "rejected": rejected}

    @app.post("/api/discover")
    def discover(scrub: bool = False) -> dict:
        uploads = sorted((state.workspace / "uploads").iterdir())
        if not uploads:
            raise HTTPException(400, "no files uploaded yet")
        inventories, errors = [], []
        for path in uploads:
            parser = get_parser_for(path)
            if parser is None:
                continue
            try:
                inv = score_inventory(parser.parse(path))
                if scrub:
                    scrub_inventory(inv)
                inventories.append(inv)
            except ParserError as exc:  # per-file isolation, same as the bulk engine
                errors.append({"file": path.name, "error": str(exc)})
        state.inventories = inventories
        clusters = cluster_inventories(inventories)
        return {
            "artifacts": [
                {
                    "name": inv.artifact_name,
                    "tool": inv.source_tool.value,
                    "band": inv.complexity_band.value,
                    "score": inv.complexity_score,
                    "effort_hours": effort_hours(inv),
                    "visuals": inv.visual_count,
                    "calculations": len(inv.calculations),
                    "scripts": len(inv.scripts),
                    "security": len(inv.security),
                    "issues": [i.message for i in inv.issues],
                    "feature_flags": {k: v for k, v in inv.feature_flags.items() if v},
                }
                for inv in sorted(inventories, key=lambda i: -i.complexity_score)
            ],
            "clusters": [
                {
                    "representative": Path(c.representative).name,
                    "retire": [Path(p).name for p in c.retire_candidates],
                }
                for c in clusters
            ],
            "savings": dedup_savings_estimate(clusters, inventories),
            "errors": errors,
        }

    @app.post("/api/convert-estate")
    def convert_estate() -> dict:
        from bimigrate.emit.model_builder import build_model
        from bimigrate.emit.pbip import PbipEmitter

        if not state.inventories:
            raise HTTPException(400, "run discovery first")
        out_dir = state.workspace / "output"
        emitter = PbipEmitter(out_dir)
        results, decisions_payload = [], []
        for inv in state.inventories:
            decisions = state.engine.convert_inventory(inv)
            project = emitter.emit(inv, build_model(inv, decisions))
            tiers = {"AUTO": 0, "ASSISTED": 0, "MANUAL": 0}
            for d in decisions:
                tiers[d.tier.value] += 1
                decisions_payload.append(d.model_dump(mode="json"))
            results.append({"project": project.name, "tiers": tiers})
        (out_dir / "conversion_decisions.json").write_text(
            json.dumps(decisions_payload, indent=2), encoding="utf-8"
        )
        zip_path = state.workspace / "pbip_output.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in out_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(out_dir))
        return {
            "projects": results,
            "decisions": decisions_payload,
            "download": "/api/download/pbip_output.zip",
        }

    @app.get("/api/download/{name}")
    def download(name: str) -> FileResponse:
        path = state.workspace / Path(name).name
        if not path.is_file():
            raise HTTPException(404, "no such artifact")
        return FileResponse(path, filename=path.name)

    @app.post("/api/reset")
    def reset() -> dict:
        shutil.rmtree(state.workspace / "uploads", ignore_errors=True)
        shutil.rmtree(state.workspace / "output", ignore_errors=True)
        (state.workspace / "uploads").mkdir()
        (state.workspace / "output").mkdir()
        state.inventories = []
        return {"ok": True}

    # -- playgrounds -------------------------------------------------------------

    @app.post("/api/convert-expression")
    def convert_expression(payload: ExpressionIn) -> dict:
        if payload.tool not in ("tableau", "spotfire", "qlikview", "qliksense"):
            raise HTTPException(400, "unknown source tool")
        engine = ConversionEngine(state.kb, table_hint=payload.table_hint)
        decision = engine.convert_expression(payload.tool, payload.expression, artifact="playground")
        result = decision.model_dump(mode="json")
        if decision.rule_id and (rule := state.kb.get_rule(decision.rule_id)):
            result["cited_rule"] = rule.model_dump()
        return result

    @app.post("/api/convert-script")
    def convert_script(payload: ScriptIn) -> dict:
        result = LoadScriptConverter().convert(payload.script)
        return {
            "queries": [
                {"name": q.name, "m_code": q.m_code, "confidence": q.confidence, "annotations": q.annotations}
                for q in result.queries
            ],
            "unconverted": result.unconverted,
            "annotations": result.annotations,
            "conversion_rate": result.conversion_rate,
        }

    @app.post("/api/classify-script")
    def classify_script(payload: IronPythonIn) -> dict:
        result = classify_ironpython(payload.name, payload.code)
        return {
            "primary_intent": result.primary_intent,
            "intents": result.intents,
            "recommendation": result.recommendation,
            "confidence": result.confidence,
            "tier": result.tier,
            "api_calls": result.api_calls,
        }

    # -- repositories -----------------------------------------------------------------

    @app.get("/api/mappings")
    def mappings(tool: str | None = None, layer: str | None = None, tier: str | None = None) -> list[dict]:
        rows = state.mappings.for_tool(tool) if tool else state.mappings.all()
        out = []
        for m in rows:
            row_tier = tier_for_confidence(m.confidence_score).value
            if layer and m.source_layer != layer:
                continue
            if tier and row_tier != tier:
                continue
            out.append(m.model_dump() | {"tier": row_tier})
        return out

    @app.get("/api/rules")
    def rules(tool: str, query: str = "") -> list[dict]:
        rows = state.kb.rules_for_tool(tool)
        needle = query.lower()
        return [
            r.model_dump()
            for r in rows
            if not needle or needle in r.rule_id.lower() or needle in r.source_example.lower()
        ][:200]

    @app.post("/api/benchmark")
    def benchmark() -> dict:
        from bimigrate.validate.benchmark import run_benchmark

        result = run_benchmark(kb=state.kb)
        return result.to_dict()

    return app


def run(host: str = "127.0.0.1", port: int = 8400) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
