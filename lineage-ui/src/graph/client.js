// One interface, two backings — the same split the Python side makes.
//
//   apiClient    talks to the FastAPI server (`pbilineage serve`)
//   localClient  answers from a graph.json held in this browser tab
//
// The hosted build only ever uses localClient: a static page cannot hold a
// service-principal secret, and the admin APIs it would need are not callable
// from a browser. Scanning stays on the machine that owns the credentials.

import {
  countNeighbours,
  impact as localImpact,
  loadGraph,
  neighbours as localNeighbours,
  search as localSearch,
  stats as localStats,
  traverse,
} from "./queries";

async function request(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== "")
  ).toString();
  const response = await fetch(query ? `${path}?${query}` : path);
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.json();
}

const encode = (id) => `/api/nodes/${encodeURIComponent(id)}`;

export const apiClient = {
  mode: "api",
  label: "Live API",
  stats: () => request("/api/stats"),
  workspaces: () => request("/api/workspaces"),
  warnings: () => request("/api/warnings", { limit: 200 }),
  search: (q, kind, limit = 40) => request("/api/search", { q, kinds: kind, limit }),
  node: (id) => request(encode(id)),
  expand: (id, containment = true) => request(`${encode(id)}/expand`, { containment }),
  lineage: (id, direction, depth, minConfidence) =>
    request(`/api/lineage/${encodeURIComponent(id)}`, {
      direction,
      depth,
      min_confidence: minConfidence,
    }),
  impact: (id, depth = 8) => request(`/api/impact/${encodeURIComponent(id)}`, { depth }),
};

/** Build a client over an already-parsed graph.json document. */
export function createLocalClient(document, label = "Loaded graph") {
  const graph = loadGraph(document);

  const asSubgraph = (result) => ({
    root: result.root,
    truncated: result.truncated,
    nodes: [...result.nodeIds].map((id) => graph.nodes.get(id)).filter(Boolean),
    edges: result.edges,
    meta: {
      direction: result.direction,
      depths: result.depths,
      confidence: result.confidence,
    },
  });

  const requireNode = (id) => {
    if (!graph.nodes.has(id)) throw new Error(`No node with id '${id}'`);
  };

  return {
    mode: "local",
    label,
    graph,
    stats: async () => localStats(graph),
    workspaces: async () =>
      [...graph.nodes.values()]
        .filter((node) => node.kind === "Workspace")
        .sort((a, b) => (a.name || "").localeCompare(b.name || "")),
    warnings: async () => ({ total: graph.warnings.length, warnings: graph.warnings }),
    search: async (q, kind, limit = 40) => {
      const results = localSearch(graph, q, kind, limit);
      return { query: q, count: results.length, results };
    },
    node: async (id) => {
      requireNode(id);
      const { upstreamCount, downstreamCount } = countNeighbours(graph, id);
      return {
        node: graph.nodes.get(id),
        upstream_count: upstreamCount,
        downstream_count: downstreamCount,
      };
    },
    expand: async (id, containment = true) => {
      requireNode(id);
      const result = localNeighbours(graph, id, containment);
      return { ...result, truncated: false, meta: { hop: 1 } };
    },
    lineage: async (id, direction, depth, minConfidence) => {
      requireNode(id);
      return asSubgraph(
        traverse(graph, id, {
          direction,
          depth,
          minConfidence: minConfidence || null,
        })
      );
    },
    impact: async (id, depth = 8) => {
      requireNode(id);
      return localImpact(graph, id, depth);
    },
  };
}

/** Fetch a graph.json shipped alongside the page (the bundled demo). */
export async function loadBundledGraph(url, label) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
  return createLocalClient(await response.json(), label);
}

/** Read a graph.json the user picked. The file never leaves the browser. */
export async function loadGraphFile(file) {
  const text = await file.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (exc) {
    throw new Error(`${file.name} is not valid JSON: ${exc.message}`);
  }
  return createLocalClient(parsed, file.name);
}

/**
 * Decide what to talk to on startup.
 * A served build asks its own API; the hosted build has none, so it falls
 * back to the bundled demo without a pointless failed request.
 */
export async function autoDetectClient(demoUrl, { preferApi }) {
  if (preferApi) {
    try {
      const health = await request("/api/health");
      if (health?.status === "ok") return apiClient;
    } catch {
      // no server here — fall through to the bundled demo
    }
  }
  return loadBundledGraph(demoUrl, "Demo tenant");
}
