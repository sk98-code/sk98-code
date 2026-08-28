// Graph queries, client-side.
//
// This is a port of pbilineage/graph/traversal.py and the search/impact parts
// of graph/store.py, so the hosted viewer can answer the same questions with
// no server. The two implementations have to agree, so the rules they share
// are restated here rather than reinvented:
//
//   * `derives_from` runs from the derived object to its input, so its TARGET
//     is upstream of its source.
//   * `used_in` runs from the producer to the consumer, so its SOURCE is
//     upstream of its target.
//   * A path's confidence is the weakest link on it. One opaque hop makes the
//     whole chain opaque.

export const CONFIDENCE_RANK = { opaque: 0, heuristic: 1, resolved: 2 };
const LINEAGE_KINDS = new Set(["derives_from", "used_in"]);

export function weakest(a, b) {
  return CONFIDENCE_RANK[a] <= CONFIDENCE_RANK[b] ? a : b;
}

/** Normalize a scanned graph.json into the shape the queries want. */
export function loadGraph(document) {
  if (!document || typeof document !== "object") {
    throw new Error("That file is not a lineage graph.");
  }
  const rawNodes = document.nodes;
  if (!rawNodes || typeof rawNodes !== "object" || Array.isArray(rawNodes)) {
    throw new Error(
      "That file has no 'nodes' object. Produce one with: pbilineage scan"
    );
  }
  const nodes = new Map(Object.entries(rawNodes));
  const edges = Array.isArray(document.edges) ? document.edges : [];

  // Drop edges pointing at nodes the file does not contain, so a truncated or
  // hand-edited graph renders instead of throwing.
  const kept = edges.filter((edge) => nodes.has(edge.source) && nodes.has(edge.target));

  const adjacency = buildAdjacency(kept);
  return {
    nodes,
    edges: kept,
    adjacency,
    warnings: Array.isArray(document.warnings) ? document.warnings : [],
    scannedAt: document.scanned_at || "",
    droppedEdges: edges.length - kept.length,
  };
}

function buildAdjacency(edges) {
  const upstream = new Map();
  const downstream = new Map();
  const push = (map, key, value) => {
    const list = map.get(key);
    if (list) list.push(value);
    else map.set(key, [value]);
  };

  for (const edge of edges) {
    let producer;
    let consumer;
    if (edge.kind === "derives_from") {
      producer = edge.target;
      consumer = edge.source;
    } else {
      // used_in, and containment, where the parent reads as the producer
      producer = edge.source;
      consumer = edge.target;
    }
    push(upstream, consumer, { node: producer, edge });
    push(downstream, producer, { node: consumer, edge });
  }
  return { upstream, downstream };
}

/**
 * Breadth-first walk from `root`, following data flow.
 * Mirrors traversal.traverse, including confidence propagation and the
 * node cap that marks a result truncated.
 */
export function traverse(graph, root, options = {}) {
  const {
    direction = "upstream",
    depth = 3,
    minConfidence = null,
    includeContainment = false,
    maxNodes = 2000,
  } = options;

  const result = {
    root,
    direction,
    nodeIds: new Set(),
    edges: [],
    depths: {},
    confidence: {},
    truncated: false,
  };
  if (!graph.nodes.has(root)) return result;

  const directions = [];
  if (direction === "upstream" || direction === "both") directions.push(graph.adjacency.upstream);
  if (direction === "downstream" || direction === "both") directions.push(graph.adjacency.downstream);

  result.nodeIds.add(root);
  result.depths[root] = 0;
  result.confidence[root] = "resolved";

  const floor = minConfidence ? CONFIDENCE_RANK[minConfidence] : -1;
  const seenEdges = new Set();
  const queue = [[root, 0]];

  while (queue.length) {
    const [current, hop] = queue.shift();
    if (hop >= depth) continue;

    for (const adjacency of directions) {
      for (const { node: neighbour, edge } of adjacency.get(current) || []) {
        const isLineage = LINEAGE_KINDS.has(edge.kind);
        if (!isLineage && !includeContainment) continue;
        if (isLineage && CONFIDENCE_RANK[edge.confidence] < floor) continue;
        if (result.nodeIds.size >= maxNodes && !result.nodeIds.has(neighbour)) {
          result.truncated = true;
          continue;
        }

        const key = `${edge.source}|${edge.target}|${edge.kind}`;
        if (!seenEdges.has(key)) {
          seenEdges.add(key);
          result.edges.push(edge);
        }

        const pathConfidence = weakest(
          result.confidence[current] || "resolved",
          isLineage ? edge.confidence : "resolved"
        );
        const known = result.confidence[neighbour];
        if (known === undefined || CONFIDENCE_RANK[pathConfidence] > CONFIDENCE_RANK[known]) {
          result.confidence[neighbour] = pathConfidence;
        }

        if (!result.nodeIds.has(neighbour)) {
          result.nodeIds.add(neighbour);
          result.depths[neighbour] = hop + 1;
          queue.push([neighbour, hop + 1]);
        }
      }
    }
  }
  return result;
}

/** One hop in every direction — what expand-on-click needs. */
export function neighbours(graph, nodeId, includeContainment = true) {
  if (!graph.nodes.has(nodeId)) return { root: nodeId, nodes: [], edges: [] };
  const edges = graph.edges.filter(
    (edge) =>
      (edge.source === nodeId || edge.target === nodeId) &&
      (includeContainment || edge.kind !== "contains")
  );
  const ids = new Set([nodeId]);
  for (const edge of edges) {
    ids.add(edge.source);
    ids.add(edge.target);
  }
  return {
    root: nodeId,
    nodes: [...ids].map((id) => graph.nodes.get(id)).filter(Boolean),
    edges,
  };
}

/** Name search, ranked exact -> prefix -> substring -> qualified name. */
export function search(graph, query, kind = "", limit = 50) {
  const needle = (query || "").trim().toLowerCase();
  const matches = [];

  for (const node of graph.nodes.values()) {
    if (kind && node.kind !== kind) continue;
    const name = (node.name || "").toLowerCase();
    const qualified = (node.qualified_name || "").toLowerCase();

    let score;
    if (!needle) score = 3;
    else if (name === needle) score = 0;
    else if (name.startsWith(needle)) score = 1;
    else if (name.includes(needle)) score = 2;
    else if (qualified.includes(needle)) score = 3;
    else continue;

    matches.push([score, node]);
  }

  matches.sort((a, b) => {
    if (a[0] !== b[0]) return a[0] - b[0];
    const left = a[1].qualified_name || a[1].name || "";
    const right = b[1].qualified_name || b[1].name || "";
    return left.localeCompare(right);
  });
  return matches.slice(0, limit).map(([, node]) => node);
}

const AFFECTED_KINDS = new Set(["Report", "Visual", "Page", "Measure"]);

/** "If this changes, what breaks?" — mirrors traversal.impact_summary. */
export function impact(graph, root, depth = 6) {
  const result = traverse(graph, root, { direction: "downstream", depth });
  const byKind = {};
  const byConfidence = {};
  const affected = [];

  for (const nodeId of [...result.nodeIds].sort()) {
    if (nodeId === root) continue;
    const node = graph.nodes.get(nodeId);
    if (!node) continue;

    byKind[node.kind] = (byKind[node.kind] || 0) + 1;
    const confidence = result.confidence[nodeId] || "opaque";
    byConfidence[confidence] = (byConfidence[confidence] || 0) + 1;

    if (AFFECTED_KINDS.has(node.kind)) {
      affected.push({
        id: node.id,
        kind: node.kind,
        name: node.name,
        qualified_name: node.qualified_name,
        workspace_id: node.workspace_id,
        depth: result.depths[nodeId] || 0,
        confidence,
      });
    }
  }

  affected.sort(
    (a, b) =>
      a.depth - b.depth || (a.qualified_name || "").localeCompare(b.qualified_name || "")
  );

  const rootNode = graph.nodes.get(root);
  return {
    root: {
      id: root,
      name: rootNode?.name || "",
      kind: rootNode?.kind || "",
      qualified_name: rootNode?.qualified_name || "",
    },
    total_downstream: result.nodeIds.size - 1,
    by_kind: sortedObject(byKind),
    by_confidence: sortedObject(byConfidence),
    affected,
    truncated: result.truncated,
  };
}

export function stats(graph) {
  const byKind = {};
  const byConfidence = {};
  for (const node of graph.nodes.values()) {
    byKind[node.kind] = (byKind[node.kind] || 0) + 1;
  }
  for (const edge of graph.edges) {
    if (LINEAGE_KINDS.has(edge.kind)) {
      byConfidence[edge.confidence] = (byConfidence[edge.confidence] || 0) + 1;
    }
  }
  return {
    nodes: graph.nodes.size,
    edges: graph.edges.length,
    nodes_by_kind: sortedObject(byKind),
    lineage_edges_by_confidence: sortedObject(byConfidence),
    warnings: graph.warnings.length,
    scanned_at: graph.scannedAt,
    backend: "browser",
  };
}

export function countNeighbours(graph, nodeId) {
  let upstreamCount = 0;
  let downstreamCount = 0;
  for (const edge of graph.edges) {
    if (!LINEAGE_KINDS.has(edge.kind)) continue;
    if (edge.kind === "derives_from") {
      if (edge.source === nodeId) upstreamCount += 1;
      if (edge.target === nodeId) downstreamCount += 1;
    } else {
      if (edge.target === nodeId) upstreamCount += 1;
      if (edge.source === nodeId) downstreamCount += 1;
    }
  }
  return { upstreamCount, downstreamCount };
}

function sortedObject(object) {
  return Object.fromEntries(Object.entries(object).sort(([a], [b]) => a.localeCompare(b)));
}
