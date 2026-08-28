"""The lineage API.

Everything the UI needs, and nothing that assumes a particular backend: the
same routes work over the in-memory JSON store and over Neo4j.

    GET /api/health                 liveness + which backend is behind it
    GET /api/stats                  node/edge counts, confidence breakdown
    GET /api/workspaces             workspaces with their capacity tier
    GET /api/search?q=              find columns/measures/reports by name
    GET /api/nodes/{id}             one node with its immediate neighbours
    GET /api/nodes/{id}/expand      one hop out, for expand-on-click
    GET /api/lineage/{id}           upstream / downstream / both subgraph
    GET /api/impact/{id}            "what breaks if this changes"
    GET /api/warnings               where the scan could not see clearly
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pbilineage import __version__
from pbilineage.graph.store import GraphStore, InMemoryGraphStore
from pbilineage.models import Confidence, LineageNode, NodeKind

__all__ = ["create_app"]

#: where the built UI lands if it has been built (`npm run build` in lineage-ui)
UI_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def _node_payload(node: LineageNode) -> dict[str, Any]:
    return node.model_dump(mode="json")


def _parse_kinds(raw: str | None) -> list[NodeKind] | None:
    if not raw:
        return None
    kinds: list[NodeKind] = []
    for token in raw.split(","):
        name = token.strip()
        if not name:
            continue
        try:
            kinds.append(NodeKind(name))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"unknown node kind '{name}'; expected one of "
                f"{', '.join(k.value for k in NodeKind)}",
            ) from None
    return kinds or None


def _parse_confidence(raw: str | None) -> Confidence | None:
    if not raw:
        return None
    try:
        return Confidence(raw.strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"unknown confidence '{raw}'; expected one of "
            f"{', '.join(c.value for c in Confidence)}",
        ) from None


def create_app(store: GraphStore | None = None, ui_dist: Path | None = None) -> FastAPI:
    """Build the app around a store. Tests pass an in-memory one."""
    graph_store: GraphStore = store or InMemoryGraphStore()
    app = FastAPI(
        title="Power BI / Fabric column lineage",
        version=__version__,
        description=(
            "Column-level lineage across a Power BI tenant: source column -> Power Query "
            "-> semantic model -> report visual, with a confidence tag on every edge."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.state.store = graph_store

    def current_store() -> GraphStore:
        return app.state.store

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        stats = current_store().stats()
        return {
            "status": "ok",
            "version": __version__,
            "backend": stats.get("backend", "in-memory"),
            "nodes": stats.get("nodes", 0),
        }

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return current_store().stats()

    @app.get("/api/workspaces")
    def workspaces() -> list[dict[str, Any]]:
        store_ = current_store()
        if isinstance(store_, InMemoryGraphStore):
            return [_node_payload(node) for node in store_.workspaces()]
        return [_node_payload(node) for node in store_.search("", [NodeKind.WORKSPACE], limit=500)]

    @app.get("/api/warnings")
    def warnings(limit: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
        store_ = current_store()
        items: Iterable[str] = store_.warnings() if isinstance(store_, InMemoryGraphStore) else []
        listed = list(items)
        return {"total": len(listed), "warnings": listed[:limit]}

    @app.get("/api/search")
    def search(
        q: str = Query("", description="name or qualified-name substring"),
        kinds: str | None = Query(None, description="comma-separated node kinds"),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        nodes = current_store().search(q, _parse_kinds(kinds), limit)
        return {"query": q, "count": len(nodes), "results": [_node_payload(n) for n in nodes]}

    @app.get("/api/nodes/{node_id:path}/expand")
    def expand(
        node_id: str,
        containment: bool = Query(True, description="include structural parent/child edges"),
    ) -> dict[str, Any]:
        store_ = current_store()
        if store_.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"no node with id '{node_id}'")
        return store_.neighbours(node_id, include_containment=containment).to_dict()

    @app.get("/api/nodes/{node_id:path}")
    def node(node_id: str) -> dict[str, Any]:
        store_ = current_store()
        found = store_.get_node(node_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no node with id '{node_id}'")
        neighbours = store_.neighbours(node_id)
        return {
            "node": _node_payload(found),
            "upstream_count": sum(
                1
                for edge in neighbours.edges
                if (edge.kind.value == "derives_from" and edge.source == node_id)
                or (edge.kind.value == "used_in" and edge.target == node_id)
            ),
            "downstream_count": sum(
                1
                for edge in neighbours.edges
                if (edge.kind.value == "derives_from" and edge.target == node_id)
                or (edge.kind.value == "used_in" and edge.source == node_id)
            ),
        }

    @app.get("/api/lineage/{node_id:path}")
    def lineage(
        node_id: str,
        direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
        depth: int = Query(3, ge=1, le=12),
        min_confidence: str | None = Query(
            None, description="resolved | heuristic | opaque — prune weaker edges"
        ),
    ) -> dict[str, Any]:
        store_ = current_store()
        if store_.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"no node with id '{node_id}'")
        subgraph = store_.lineage(
            node_id,
            direction=direction,  # type: ignore[arg-type]
            depth=depth,
            min_confidence=_parse_confidence(min_confidence),
        )
        return subgraph.to_dict()

    @app.get("/api/impact/{node_id:path}")
    def impact(node_id: str, depth: int = Query(6, ge=1, le=12)) -> dict[str, Any]:
        store_ = current_store()
        if store_.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"no node with id '{node_id}'")
        return store_.impact(node_id, depth=depth)

    _mount_ui(app, ui_dist or UI_DIST)
    return app


def _mount_ui(app: FastAPI, dist: Path) -> None:
    """Serve the built React app when it is present; stay headless when not."""
    if not dist.is_dir():

        @app.get("/")
        def ui_missing() -> dict[str, str]:
            return {
                "message": "API is running. The UI is not built.",
                "build": "cd lineage-ui && npm install && npm run build",
                "docs": "/docs",
            }

        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(dist / "index.html"))
