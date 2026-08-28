"""Upstream / downstream traversal and impact analysis.

Direction is defined once, here, so nothing else has to remember which way an
edge points:

* `derives_from` runs from the derived object to its input, so its **target**
  is upstream of its source.
* `used_in` runs from the producer to the consumer, so its **source** is
  upstream of its target.

A path's confidence is the weakest link on it: one opaque hop makes the whole
chain opaque, which is exactly what a reviewer needs to know before trusting
an impact answer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Literal

from pbilineage.models import (
    CONFIDENCE_RANK,
    Confidence,
    EdgeKind,
    LINEAGE_EDGES,
    LineageEdge,
    LineageGraph,
    NodeKind,
)

__all__ = ["Direction", "TraversalResult", "impact_summary", "traverse", "weakest"]

Direction = Literal["upstream", "downstream", "both"]


def weakest(*confidences: Confidence) -> Confidence:
    """The lowest-confidence value among the arguments."""
    if not confidences:
        return Confidence.RESOLVED
    return min(confidences, key=lambda c: CONFIDENCE_RANK[c])


@dataclass(slots=True)
class TraversalResult:
    root: str
    direction: Direction
    node_ids: set[str] = field(default_factory=set)
    edges: list[LineageEdge] = field(default_factory=list)
    #: node id -> hop count from the root
    depths: dict[str, int] = field(default_factory=dict)
    #: node id -> weakest confidence on the best path from the root
    confidence: dict[str, Confidence] = field(default_factory=dict)
    truncated: bool = False


def _neighbours(
    graph: LineageGraph,
    include_containment: bool,
) -> tuple[dict[str, list[tuple[str, LineageEdge]]], dict[str, list[tuple[str, LineageEdge]]]]:
    """Build (upstream, downstream) adjacency in one pass."""
    upstream: dict[str, list[tuple[str, LineageEdge]]] = {}
    downstream: dict[str, list[tuple[str, LineageEdge]]] = {}
    for edge in graph.edges:
        if edge.kind is EdgeKind.CONTAINS and not include_containment:
            continue
        if edge.kind is EdgeKind.DERIVES_FROM:
            producer, consumer = edge.target, edge.source
        elif edge.kind is EdgeKind.USED_IN:
            producer, consumer = edge.source, edge.target
        else:  # containment: treat the parent as "upstream" for expansion
            producer, consumer = edge.source, edge.target
        upstream.setdefault(consumer, []).append((producer, edge))
        downstream.setdefault(producer, []).append((consumer, edge))
    return upstream, downstream


def traverse(
    graph: LineageGraph,
    root: str,
    direction: Direction = "upstream",
    depth: int = 4,
    min_confidence: Confidence | None = None,
    include_containment: bool = False,
    max_nodes: int = 2000,
) -> TraversalResult:
    """Breadth-first walk from `root`, following data flow.

    `min_confidence` prunes edges below the given tier, which is how the UI
    offers a "certain lineage only" view.
    """
    result = TraversalResult(root=root, direction=direction)
    if root not in graph.nodes:
        return result

    upstream, downstream = _neighbours(graph, include_containment)
    directions: list[dict[str, list[tuple[str, LineageEdge]]]] = []
    if direction in ("upstream", "both"):
        directions.append(upstream)
    if direction in ("downstream", "both"):
        directions.append(downstream)

    result.node_ids.add(root)
    result.depths[root] = 0
    result.confidence[root] = Confidence.RESOLVED

    queue: deque[tuple[str, int]] = deque([(root, 0)])
    seen_edges: set[tuple[str, str, str]] = set()

    while queue:
        current, hop = queue.popleft()
        if hop >= depth:
            continue
        for adjacency in directions:
            for neighbour, edge in adjacency.get(current, []):
                if min_confidence is not None and edge.kind in LINEAGE_EDGES:
                    if CONFIDENCE_RANK[edge.confidence] < CONFIDENCE_RANK[min_confidence]:
                        continue
                if len(result.node_ids) >= max_nodes and neighbour not in result.node_ids:
                    result.truncated = True
                    continue
                if edge.key not in seen_edges:
                    seen_edges.add(edge.key)
                    result.edges.append(edge)

                path_confidence = weakest(
                    result.confidence.get(current, Confidence.RESOLVED),
                    edge.confidence if edge.kind in LINEAGE_EDGES else Confidence.RESOLVED,
                )
                known = result.confidence.get(neighbour)
                if known is None or CONFIDENCE_RANK[path_confidence] > CONFIDENCE_RANK[known]:
                    result.confidence[neighbour] = path_confidence

                if neighbour not in result.node_ids:
                    result.node_ids.add(neighbour)
                    result.depths[neighbour] = hop + 1
                    queue.append((neighbour, hop + 1))
    return result


def impact_summary(
    graph: LineageGraph,
    root: str,
    depth: int = 6,
    kinds: Iterable[NodeKind] | None = None,
) -> dict[str, object]:
    """ "If this column changes, what breaks?" — grouped downstream reach.

    Reports and visuals are the answer people actually want, so they are
    listed explicitly rather than only counted.
    """
    result = traverse(graph, root, direction="downstream", depth=depth)
    wanted = set(kinds) if kinds else None

    by_kind: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    affected: list[dict[str, object]] = []

    for node_id in sorted(result.node_ids):
        if node_id == root:
            continue
        node = graph.nodes.get(node_id)
        if node is None or (wanted is not None and node.kind not in wanted):
            continue
        by_kind[node.kind.value] = by_kind.get(node.kind.value, 0) + 1
        confidence = result.confidence.get(node_id, Confidence.OPAQUE)
        by_confidence[confidence.value] = by_confidence.get(confidence.value, 0) + 1
        if node.kind in (NodeKind.REPORT, NodeKind.VISUAL, NodeKind.PAGE, NodeKind.MEASURE):
            affected.append(
                {
                    "id": node.id,
                    "kind": node.kind.value,
                    "name": node.name,
                    "qualified_name": node.qualified_name,
                    "workspace_id": node.workspace_id,
                    "depth": result.depths.get(node_id, 0),
                    "confidence": confidence.value,
                }
            )

    root_node = graph.nodes.get(root)
    return {
        "root": {
            "id": root,
            "name": root_node.name if root_node else "",
            "kind": root_node.kind.value if root_node else "",
            "qualified_name": root_node.qualified_name if root_node else "",
        },
        "total_downstream": len(result.node_ids) - 1,
        "by_kind": dict(sorted(by_kind.items())),
        "by_confidence": dict(sorted(by_confidence.items())),
        "affected": sorted(affected, key=lambda item: (item["depth"], item["qualified_name"])),
        "truncated": result.truncated,
    }
