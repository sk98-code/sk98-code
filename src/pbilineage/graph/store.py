"""Graph persistence.

Two implementations sit behind `GraphStore`:

* `InMemoryGraphStore` — the whole graph in memory, persisted as JSON. This
  is what `pbilineage scan` writes and `pbilineage serve` reads, so the tool
  is usable (and testable) with no database running.
* `Neo4jGraphStore` — the same interface against Neo4j / Memgraph, for tenant
  scale and ad-hoc Cypher.

The API layer only ever talks to this interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from pbilineage.graph.traversal import Direction, impact_summary, traverse
from pbilineage.models import (
    Confidence,
    EdgeKind,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeKind,
)

__all__ = ["GraphStore", "InMemoryGraphStore", "Subgraph"]


@dataclass(slots=True)
class Subgraph:
    """What the UI renders: nodes, edges, and per-node traversal metadata."""

    nodes: list[LineageNode] = field(default_factory=list)
    edges: list[LineageEdge] = field(default_factory=list)
    root: str = ""
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "truncated": self.truncated,
            "nodes": [node.model_dump(mode="json") for node in self.nodes],
            "edges": [edge.model_dump(mode="json") for edge in self.edges],
            "meta": self.meta,
        }


class GraphStore(Protocol):
    def write(self, graph: LineageGraph) -> None: ...

    def get_node(self, node_id: str) -> LineageNode | None: ...

    def search(
        self, query: str, kinds: Iterable[NodeKind] | None = None, limit: int = 50
    ) -> list[LineageNode]: ...

    def lineage(
        self,
        node_id: str,
        direction: Direction = "upstream",
        depth: int = 3,
        min_confidence: Confidence | None = None,
    ) -> Subgraph: ...

    def neighbours(self, node_id: str, include_containment: bool = True) -> Subgraph: ...

    def impact(self, node_id: str, depth: int = 6) -> dict[str, Any]: ...

    def stats(self) -> dict[str, Any]: ...


class InMemoryGraphStore:
    """Whole-graph-in-memory store with JSON persistence."""

    def __init__(self, graph: LineageGraph | None = None) -> None:
        self.graph = graph or LineageGraph()

    # -- persistence ------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> InMemoryGraphStore:
        file = Path(path)
        if not file.is_file():
            raise FileNotFoundError(f"no graph at {file}; run 'pbilineage scan' first")
        payload = json.loads(file.read_text(encoding="utf-8"))
        return cls(LineageGraph.model_validate(payload))

    def save(self, path: str | Path) -> Path:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(self.graph.model_dump(mode="json"), indent=2), encoding="utf-8")
        return file

    # -- GraphStore -------------------------------------------------------
    def write(self, graph: LineageGraph) -> None:
        """Merge a scan result in (incremental scans replace only what changed)."""
        self.graph.extend(graph)
        self.graph.scanned_at = graph.scanned_at

    def replace_workspace(self, workspace_id: str, graph: LineageGraph) -> None:
        """Drop everything belonging to a workspace, then merge the new scan.

        Incremental scans need this: a deleted measure must disappear, not
        linger from the previous run.
        """
        stale = {node_id for node_id, node in self.graph.nodes.items() if node.workspace_id == workspace_id}
        if stale:
            self.graph.nodes = {
                node_id: node for node_id, node in self.graph.nodes.items() if node_id not in stale
            }
            self.graph.edges = [
                edge for edge in self.graph.edges if edge.source not in stale and edge.target not in stale
            ]
        self.write(graph)

    def get_node(self, node_id: str) -> LineageNode | None:
        return self.graph.nodes.get(node_id)

    def search(
        self, query: str, kinds: Iterable[NodeKind] | None = None, limit: int = 50
    ) -> list[LineageNode]:
        needle = (query or "").strip().lower()
        wanted = set(kinds) if kinds else None
        matches: list[tuple[int, LineageNode]] = []
        for node in self.graph.nodes.values():
            if wanted is not None and node.kind not in wanted:
                continue
            name = node.name.lower()
            qualified = node.qualified_name.lower()
            if not needle:
                score = 3
            elif name == needle:
                score = 0
            elif name.startswith(needle):
                score = 1
            elif needle in name:
                score = 2
            elif needle in qualified:
                score = 3
            else:
                continue
            matches.append((score, node))
        matches.sort(key=lambda item: (item[0], item[1].qualified_name or item[1].name))
        return [node for _, node in matches[:limit]]

    def lineage(
        self,
        node_id: str,
        direction: Direction = "upstream",
        depth: int = 3,
        min_confidence: Confidence | None = None,
    ) -> Subgraph:
        result = traverse(
            self.graph, node_id, direction=direction, depth=depth, min_confidence=min_confidence
        )
        nodes = [self.graph.nodes[n] for n in result.node_ids if n in self.graph.nodes]
        return Subgraph(
            nodes=nodes,
            edges=result.edges,
            root=node_id,
            truncated=result.truncated,
            meta={
                "direction": direction,
                "depth": depth,
                "depths": result.depths,
                "confidence": {k: v.value for k, v in result.confidence.items()},
            },
        )

    def neighbours(self, node_id: str, include_containment: bool = True) -> Subgraph:
        """One hop in every direction — what the UI's expand-on-click needs."""
        if node_id not in self.graph.nodes:
            return Subgraph(root=node_id)
        edges = [
            edge
            for edge in self.graph.edges
            if (edge.source == node_id or edge.target == node_id)
            and (include_containment or edge.kind is not EdgeKind.CONTAINS)
        ]
        ids = {node_id}
        for edge in edges:
            ids.add(edge.source)
            ids.add(edge.target)
        return Subgraph(
            nodes=[self.graph.nodes[i] for i in ids if i in self.graph.nodes],
            edges=edges,
            root=node_id,
            meta={"hop": 1},
        )

    def impact(self, node_id: str, depth: int = 6) -> dict[str, Any]:
        return impact_summary(self.graph, node_id, depth=depth)

    def stats(self) -> dict[str, Any]:
        return self.graph.stats()

    # -- extras used by the API -------------------------------------------
    def workspaces(self) -> list[LineageNode]:
        return sorted(
            (n for n in self.graph.nodes.values() if n.kind is NodeKind.WORKSPACE),
            key=lambda n: n.name.lower(),
        )

    def warnings(self) -> list[str]:
        return list(self.graph.warnings)
