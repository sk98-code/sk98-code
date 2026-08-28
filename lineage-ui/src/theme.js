// One place for the visual vocabulary, so the graph, the legend and the
// detail panel never disagree about what a colour or a line style means.

export const NODE_KINDS = {
  DataSource: { label: "Data source", color: "#8b5cf6", icon: "▤" },
  Dataflow: { label: "Dataflow", color: "#0ea5e9", icon: "⇄" },
  SemanticModel: { label: "Semantic model", color: "#2563eb", icon: "◈" },
  Table: { label: "Table", color: "#0f766e", icon: "▦" },
  Column: { label: "Column", color: "#059669", icon: "▪" },
  Measure: { label: "Measure", color: "#d97706", icon: "ƒ" },
  Report: { label: "Report", color: "#dc2626", icon: "▣" },
  Page: { label: "Page", color: "#e11d48", icon: "▢" },
  Visual: { label: "Visual", color: "#db2777", icon: "◕" },
  Workspace: { label: "Workspace", color: "#475569", icon: "◰" },
};

// Confidence is the whole point of the tool, so it gets the strongest visual
// signal available on an edge: line style plus colour.
export const CONFIDENCE = {
  resolved: {
    label: "Resolved",
    color: "#16a34a",
    dash: undefined,
    hint: "The engine resolved this (DISCOVER_CALC_DEPENDENCY, or a literal report binding).",
  },
  heuristic: {
    label: "Heuristic",
    color: "#d97706",
    dash: "6 3",
    hint: "Our parser recognised the construct and inferred the reference.",
  },
  opaque: {
    label: "Opaque",
    color: "#dc2626",
    dash: "2 4",
    hint: "We saw something we will not guess at. Treat this hop as unverified.",
  },
};

export const kindStyle = (kind) =>
  NODE_KINDS[kind] || { label: kind, color: "#64748b", icon: "•" };

export const confidenceStyle = (confidence) =>
  CONFIDENCE[confidence] || CONFIDENCE.heuristic;
