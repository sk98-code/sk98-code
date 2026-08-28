export async function api(path, params = {}) {
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

const node = (id) => `/api/nodes/${encodeURIComponent(id)}`;

export const getStats = () => api("/api/stats");
export const getWorkspaces = () => api("/api/workspaces");
export const getWarnings = () => api("/api/warnings", { limit: 200 });
export const search = (q, kinds, limit = 40) => api("/api/search", { q, kinds, limit });
export const getNode = (id) => api(node(id));
export const expandNode = (id, containment = true) =>
  api(`${node(id)}/expand`, { containment });
export const getLineage = (id, direction, depth, minConfidence) =>
  api(`/api/lineage/${encodeURIComponent(id)}`, {
    direction,
    depth,
    min_confidence: minConfidence,
  });
export const getImpact = (id, depth = 8) =>
  api(`/api/impact/${encodeURIComponent(id)}`, { depth });
