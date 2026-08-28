"""Neo4j / Memgraph backing store.

Writes are idempotent MERGEs on the deterministic node ids, so re-scanning a
workspace updates in place instead of duplicating. Traversal is expressed as
variable-length Cypher rather than pulled into Python, which is the reason to
use a graph database for this at tenant scale.

The `neo4j` driver is an optional dependency: `pip install pbilineage[neo4j]`.
"""

from __future__ import annotations

from typing import Any, Iterable

from pbilineage.graph.store import Subgraph
from pbilineage.graph.traversal import Direction
from pbilineage.models import (
    CONFIDENCE_RANK,
    Confidence,
    EdgeKind,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeKind,
)

__all__ = ["Neo4jGraphStore", "CONSTRAINTS"]

CONSTRAINTS = (
    "CREATE CONSTRAINT lineage_node_id IF NOT EXISTS " "FOR (n:LineageNode) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX lineage_node_kind IF NOT EXISTS FOR (n:LineageNode) ON (n.kind)",
    "CREATE INDEX lineage_node_name IF NOT EXISTS FOR (n:LineageNode) ON (n.name)",
    "CREATE INDEX lineage_node_workspace IF NOT EXISTS FOR (n:LineageNode) ON (n.workspace_id)",
)

#: one relationship type per edge kind keeps Cypher readable
REL_TYPES = {
    EdgeKind.DERIVES_FROM: "DERIVES_FROM",
    EdgeKind.USED_IN: "USED_IN",
    EdgeKind.CONTAINS: "CONTAINS",
}

#: `derives_from` and `used_in` describe the same flow in opposite directions,
#: and a variable-length Cypher pattern cannot mix per-hop directions. So every
#: lineage edge is *also* written as a single canonical FLOWS_TO relationship
#: pointing upstream -> downstream. Traversal uses FLOWS_TO; the typed
#: relationships are kept because they are what the data model means.
FLOW_REL = "FLOWS_TO"

MERGE_NODES = """
UNWIND $rows AS row
MERGE (n:LineageNode {id: row.id})
SET n += row.props, n.kind = row.kind, n.name = row.name,
    n.qualified_name = row.qualified_name, n.workspace_id = row.workspace_id
"""


def _edge_statement(rel_type: str) -> str:
    return f"""
UNWIND $rows AS row
MATCH (a:LineageNode {{id: row.source}})
MATCH (b:LineageNode {{id: row.target}})
MERGE (a)-[r:{rel_type}]->(b)
SET r += row.props
"""


def _flatten(properties: dict[str, Any]) -> dict[str, Any]:
    """Neo4j properties must be primitives or arrays of primitives."""
    flat: dict[str, Any] = {}
    for key, value in properties.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            flat[key] = value
        elif isinstance(value, (list, tuple)):
            flat[key] = [str(item) for item in value]
        else:
            flat[key] = str(value)
    return flat


class Neo4jGraphStore:
    """`GraphStore` over Neo4j. Requires the `neo4j` Python driver."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
        batch_size: int = 1000,
    ) -> None:
        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised by install shape
            raise RuntimeError(
                "the 'neo4j' package is required for the Neo4j store; "
                "install pbilineage[neo4j] or use the in-memory JSON store"
            ) from exc
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database
        self._batch_size = batch_size

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Neo4jGraphStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _run(self, statement: str, **parameters: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            return [record.data() for record in session.run(statement, **parameters)]

    def ensure_schema(self) -> None:
        for statement in CONSTRAINTS:
            self._run(statement)

    # -- writing ----------------------------------------------------------
    def write(self, graph: LineageGraph) -> None:
        self.ensure_schema()
        rows = [
            {
                "id": node.id,
                "kind": node.kind.value,
                "name": node.name,
                "qualified_name": node.qualified_name,
                "workspace_id": node.workspace_id,
                "props": _flatten(node.properties),
            }
            for node in graph.nodes.values()
        ]
        for start in range(0, len(rows), self._batch_size):
            self._run(MERGE_NODES, rows=rows[start : start + self._batch_size])

        by_type: dict[str, list[dict[str, Any]]] = {}
        for edge in graph.edges:
            props = _flatten(
                {
                    **edge.properties,
                    "confidence": edge.confidence.value,
                    "evidence": edge.evidence,
                    "kind": edge.kind.value,
                }
            )
            by_type.setdefault(REL_TYPES[edge.kind], []).append(
                {"source": edge.source, "target": edge.target, "props": props}
            )
            if edge.kind is EdgeKind.CONTAINS:
                continue
            # canonical flow direction: upstream -> downstream
            upstream, downstream = (
                (edge.target, edge.source)
                if edge.kind is EdgeKind.DERIVES_FROM
                else (edge.source, edge.target)
            )
            by_type.setdefault(FLOW_REL, []).append(
                {"source": upstream, "target": downstream, "props": props}
            )

        for rel_type, edge_rows in by_type.items():
            statement = _edge_statement(rel_type)
            for start in range(0, len(edge_rows), self._batch_size):
                self._run(statement, rows=edge_rows[start : start + self._batch_size])

    def delete_workspace(self, workspace_id: str) -> None:
        self._run(
            "MATCH (n:LineageNode {workspace_id: $workspace_id}) DETACH DELETE n",
            workspace_id=workspace_id,
        )

    # -- reading ----------------------------------------------------------
    def get_node(self, node_id: str) -> LineageNode | None:
        rows = self._run("MATCH (n:LineageNode {id: $id}) RETURN n", id=node_id)
        return _node_from_record(rows[0]["n"]) if rows else None

    def search(
        self, query: str, kinds: Iterable[NodeKind] | None = None, limit: int = 50
    ) -> list[LineageNode]:
        kind_values = [k.value for k in kinds] if kinds else None
        rows = self._run(
            """
            MATCH (n:LineageNode)
            WHERE ($kinds IS NULL OR n.kind IN $kinds)
              AND ($needle = '' OR toLower(n.name) CONTAINS $needle
                   OR toLower(coalesce(n.qualified_name, '')) CONTAINS $needle)
            RETURN n ORDER BY size(n.name), n.name LIMIT $limit
            """,
            needle=(query or "").strip().lower(),
            kinds=kind_values,
            limit=limit,
        )
        return [_node_from_record(row["n"]) for row in rows]

    def lineage(
        self,
        node_id: str,
        direction: Direction = "upstream",
        depth: int = 3,
        min_confidence: Confidence | None = None,
    ) -> Subgraph:
        hops = max(1, min(depth, 12))
        pattern = {
            "upstream": f"(m)-[:{FLOW_REL}*1..{hops}]->(n)",
            "downstream": f"(n)-[:{FLOW_REL}*1..{hops}]->(m)",
            "both": f"(n)-[:{FLOW_REL}*1..{hops}]-(m)",
        }[direction]

        rows = self._run(
            f"""
            MATCH (n:LineageNode {{id: $id}})
            OPTIONAL MATCH path = {pattern}
            UNWIND (CASE WHEN path IS NULL THEN [] ELSE relationships(path) END) AS rel
            RETURN DISTINCT startNode(rel) AS a, endNode(rel) AS b, properties(rel) AS props
            """,
            id=node_id,
        )

        nodes: dict[str, LineageNode] = {}
        root = self.get_node(node_id)
        if root is not None:
            nodes[root.id] = root

        minimum = CONFIDENCE_RANK[min_confidence] if min_confidence else -1
        edges: dict[tuple[str, str, str], LineageEdge] = {}
        for row in rows:
            if not row.get("a") or not row.get("b"):
                continue
            upstream_node = _node_from_record(row["a"])
            downstream_node = _node_from_record(row["b"])
            nodes[upstream_node.id] = upstream_node
            nodes[downstream_node.id] = downstream_node
            edge = _flow_edge(upstream_node.id, downstream_node.id, dict(row.get("props") or {}))
            if CONFIDENCE_RANK[edge.confidence] >= minimum:
                edges[edge.key] = edge

        return Subgraph(
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            root=node_id,
            meta={"direction": direction, "depth": depth, "backend": "neo4j"},
        )

    def neighbours(self, node_id: str, include_containment: bool = True) -> Subgraph:
        types = "DERIVES_FROM|USED_IN" + ("|CONTAINS" if include_containment else "")
        rows = self._run(
            f"""
            MATCH (n:LineageNode {{id: $id}})
            OPTIONAL MATCH (n)-[r:{types}]-(m)
            RETURN n, r, m, startNode(r).id AS source, endNode(r).id AS target, type(r) AS type
            """,
            id=node_id,
        )
        nodes: dict[str, LineageNode] = {}
        edges: list[LineageEdge] = []
        for row in rows:
            for key in ("n", "m"):
                if row.get(key):
                    node = _node_from_record(row[key])
                    nodes[node.id] = node
            if row.get("r") and row.get("type"):
                edges.append(_edge_from_record(row))
        return Subgraph(nodes=list(nodes.values()), edges=edges, root=node_id, meta={"hop": 1})

    def impact(self, node_id: str, depth: int = 6) -> dict[str, Any]:
        rows = self._run(
            f"""
            MATCH (n:LineageNode {{id: $id}})
            MATCH path = (n)-[:{FLOW_REL}*1..{max(1, min(depth, 12))}]->(m)
            RETURN DISTINCT m, [rel IN relationships(path) | rel.confidence] AS confidences
            """,
            id=node_id,
        )
        by_kind: dict[str, int] = {}
        by_confidence: dict[str, int] = {}
        affected: list[dict[str, Any]] = []
        for row in rows:
            node = _node_from_record(row["m"])
            by_kind[node.kind.value] = by_kind.get(node.kind.value, 0) + 1
            path_confidence = min(
                (Confidence(c) for c in row.get("confidences", []) if c),
                key=lambda c: CONFIDENCE_RANK[c],
                default=Confidence.RESOLVED,
            )
            by_confidence[path_confidence.value] = by_confidence.get(path_confidence.value, 0) + 1
            if node.kind in (NodeKind.REPORT, NodeKind.VISUAL, NodeKind.PAGE, NodeKind.MEASURE):
                affected.append(
                    {
                        "id": node.id,
                        "kind": node.kind.value,
                        "name": node.name,
                        "qualified_name": node.qualified_name,
                        "confidence": path_confidence.value,
                    }
                )
        return {
            "root": {"id": node_id},
            "total_downstream": len(rows),
            "by_kind": dict(sorted(by_kind.items())),
            "by_confidence": dict(sorted(by_confidence.items())),
            "affected": affected,
            "truncated": False,
        }

    def stats(self) -> dict[str, Any]:
        nodes = self._run("MATCH (n:LineageNode) RETURN n.kind AS kind, count(*) AS total ORDER BY kind")
        edges = self._run(
            f"MATCH ()-[r:{FLOW_REL}]->() "
            "RETURN r.confidence AS confidence, count(*) AS total ORDER BY confidence"
        )
        return {
            "nodes": sum(row["total"] for row in nodes),
            "nodes_by_kind": {row["kind"]: row["total"] for row in nodes},
            "lineage_edges_by_confidence": {
                row["confidence"]: row["total"] for row in edges if row["confidence"]
            },
            "backend": "neo4j",
        }


def _node_from_record(record: Any) -> LineageNode:
    data = dict(record)
    reserved = {"id", "kind", "name", "qualified_name", "workspace_id"}
    return LineageNode(
        id=str(data.get("id", "")),
        kind=NodeKind(data.get("kind", NodeKind.TABLE.value)),
        name=str(data.get("name", "")),
        qualified_name=str(data.get("qualified_name") or ""),
        workspace_id=data.get("workspace_id"),
        properties={k: v for k, v in data.items() if k not in reserved},
    )


def _edge_from_record(row: dict[str, Any]) -> LineageEdge:
    properties = dict(row.get("r") or {})
    kind = next((k for k, v in REL_TYPES.items() if v == row.get("type")), EdgeKind.DERIVES_FROM)
    properties.pop("kind", None)
    return LineageEdge(
        source=str(row.get("source", "")),
        target=str(row.get("target", "")),
        kind=kind,
        confidence=Confidence(properties.pop("confidence", Confidence.HEURISTIC.value)),
        evidence=str(properties.pop("evidence", "")),
        properties=properties,
    )


def _flow_edge(upstream: str, downstream: str, properties: dict[str, Any]) -> LineageEdge:
    """Rebuild the typed edge a FLOWS_TO relationship was derived from."""
    kind = EdgeKind(properties.pop("kind", EdgeKind.DERIVES_FROM.value))
    source, target = (downstream, upstream) if kind is EdgeKind.DERIVES_FROM else (upstream, downstream)
    return LineageEdge(
        source=source,
        target=target,
        kind=kind,
        confidence=Confidence(properties.pop("confidence", Confidence.HEURISTIC.value)),
        evidence=str(properties.pop("evidence", "")),
        properties=properties,
    )
