"""Dependency graph + reachability (build spec §6, Milestone 4).

One graph serves both directions: usage = forward reachability from roots
(visuals, filters, RLS, relationships, refresh policy, external
consumers); column-level lineage (C5) = the reverse traversal. Every edge
carries evidence — a verdict the UI cannot justify is not acceptable
output.

Edge direction: consumer → consumed ("the visual uses the column").
"""

from __future__ import annotations

from dataclasses import dataclass, field


# -- node id helpers (single-model scope; tenant scan prefixes model ids) ---
def nid_table(table: str) -> str:
    return f"table:{table}"


def nid_column(table: str, column: str) -> str:
    return f"column:{table}[{column}]"


def nid_measure(name: str, host_report: str | None = None) -> str:
    return f"measure:{name}@{host_report}" if host_report else f"measure:{name}"


def nid_hierarchy(table: str, hierarchy: str) -> str:
    return f"hierarchy:{table}/{hierarchy}"


def nid_calc_item(table: str, item: str) -> str:
    return f"calcitem:{table}/{item}"


def nid_visual(report: str, page: str, visual: str) -> str:
    return f"visual:{report}/{page}/{visual}"


def nid_page(report: str, page: str) -> str:
    return f"page:{report}/{page}"


def nid_report(report: str) -> str:
    return f"report:{report}"


def nid_role(role: str) -> str:
    return f"role:{role}"


def nid_relationship(name: str) -> str:
    return f"relationship:{name}"


def nid_partition(table: str, partition: str) -> str:
    return f"partition:{table}/{partition}"


def nid_expression(name: str) -> str:
    return f"expression:{name}"


ALL_MEASURES = "measures:*"  # wildcard target for SELECTEDMEASURE() edges


@dataclass
class Node:
    id: str
    kind: str  # table | column | measure | hierarchy | calc_item | report | page | visual | role | relationship | partition | expression
    name: str
    parent: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str  # consumer
    target: str  # consumed
    kind: str  # projects | filters | formats | sorts | relates | defines | wildcard
    confidence: str  # engine | parsed | declared | wildcard
    evidence: str


class DependencyGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.roots: dict[str, str] = {}  # node id -> reason it is a root
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}
        self._edge_set: set[Edge] = set()

    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is not None:
            return existing
        self.nodes[node.id] = node
        return node

    def add_edge(self, source: str, target: str, kind: str, confidence: str, evidence: str) -> None:
        edge = Edge(source=source, target=target, kind=kind, confidence=confidence, evidence=evidence)
        if edge in self._edge_set:
            return
        self._edge_set.add(edge)
        self.edges.append(edge)
        self._out.setdefault(source, []).append(edge)
        self._in.setdefault(target, []).append(edge)

    def mark_root(self, node_id: str, reason: str) -> None:
        self.roots.setdefault(node_id, reason)

    # -- traversal ---------------------------------------------------------

    def out_edges(self, node_id: str) -> list[Edge]:
        return self._out.get(node_id, [])

    def in_edges(self, node_id: str) -> list[Edge]:
        return self._in.get(node_id, [])

    def reachable(self, *, include_wildcard: bool = False) -> dict[str, list[Edge]]:
        """Transitive closure from every root: node id → the edges that
        first reached it (the evidence chain start). Wildcard edges are
        excluded by default — SELECTEDMEASURE() cannot resurrect a measure
        no visual references (§5.3 wildcard semantics)."""
        reached: dict[str, list[Edge]] = {root: [] for root in self.roots}
        queue = list(self.roots)
        while queue:
            current = queue.pop()
            for edge in self._out.get(current, []):
                if edge.kind == "wildcard" and not include_wildcard:
                    continue
                if edge.target not in reached:
                    reached[edge.target] = [edge]
                    queue.append(edge.target)
                elif edge not in reached[edge.target] and len(reached[edge.target]) < 8:
                    reached[edge.target].append(edge)
        return reached

    def _walk(self, node_id: str, adjacency: dict[str, list[Edge]], pick_end, max_depth: int) -> dict:
        tree: dict = {"id": node_id, "children": []}
        if max_depth <= 0:
            return tree
        seen = {node_id}

        def expand(parent: dict, current: str, depth: int) -> None:
            for edge in adjacency.get(current, []):
                other = pick_end(edge)
                child = {"id": other, "edge": edge, "children": []}
                parent["children"].append(child)
                if other not in seen and depth < max_depth:
                    seen.add(other)
                    expand(child, other, depth + 1)

        expand(tree, node_id, 1)
        return tree

    def dependency_tree(self, node_id: str, *, max_depth: int = 12) -> dict:
        """Downward tree (C4): what this node consumes, recursively."""
        return self._walk(node_id, self._out, lambda e: e.target, max_depth)

    def consumers_tree(self, node_id: str, *, max_depth: int = 12) -> dict:
        """Upward tree (C5 lineage): everything that consumes this node."""
        return self._walk(node_id, self._in, lambda e: e.source, max_depth)

    def consumers(self, node_id: str) -> set[str]:
        """Transitive set of consumer node ids (blast radius for removal)."""
        seen: set[str] = set()
        queue = [node_id]
        while queue:
            current = queue.pop()
            for edge in self._in.get(current, []):
                if edge.source not in seen:
                    seen.add(edge.source)
                    queue.append(edge.source)
        return seen

    def render_tree(self, tree: dict, *, indent: str = "") -> str:
        """Plain-text dependency tree (Milestone 4 definition of done)."""
        node = self.nodes.get(tree["id"])
        label = f"{node.kind} {node.name}" if node else tree["id"]
        edge = tree.get("edge")
        suffix = f"  [{edge.kind}/{edge.confidence}]" if edge else ""
        lines = [f"{indent}{label}{suffix}"]
        for child in tree["children"]:
            lines.append(self.render_tree(child, indent=indent + "  "))
        return "\n".join(lines)
