// The hosted viewer answers these queries with no server, so this port has to
// agree with pbilineage/graph/traversal.py. The fixture below is the same
// shape a real scan produces:
//
//   source column -> (M rename, heuristic) -> model column
//   model column  -> (DMV, resolved)       -> measure
//   measure       -> (report layout)       -> visual

import { describe, expect, it } from "vitest";
import {
  countNeighbours,
  impact,
  loadGraph,
  neighbours,
  search,
  stats,
  traverse,
  weakest,
} from "./queries";

const node = (id, kind, name, extra = {}) => ({
  id,
  kind,
  name,
  qualified_name: `Demo / ${name}`,
  properties: {},
  ...extra,
});

const DOCUMENT = {
  nodes: {
    src: node("src", "Column", "SalesAmount", { properties: { is_source: true } }),
    srcTable: node("srcTable", "Table", "dbo.FactSales"),
    col: node("col", "Column", "Amount"),
    table: node("table", "Table", "FactSales"),
    measure: node("measure", "Measure", "Total Revenue"),
    derived: node("derived", "Measure", "Margin %"),
    visual: node("visual", "Visual", "Revenue by region"),
    orphan: node("orphan", "Column", "Unused"),
  },
  edges: [
    // model column derives from the source column, through an M rename
    { source: "col", target: "src", kind: "derives_from", confidence: "heuristic", evidence: "m:rename" },
    // the measure's dependency came from the engine
    { source: "measure", target: "col", kind: "derives_from", confidence: "resolved", evidence: "DMV" },
    { source: "derived", target: "measure", kind: "derives_from", confidence: "resolved", evidence: "DMV" },
    // the visual binding is a literal in the report layout
    { source: "measure", target: "visual", kind: "used_in", confidence: "resolved", evidence: "layout" },
    // structure, not data flow
    { source: "table", target: "col", kind: "contains", confidence: "resolved", evidence: "metadata" },
    { source: "srcTable", target: "src", kind: "contains", confidence: "resolved", evidence: "metadata" },
  ],
  warnings: ["one workspace is on shared capacity"],
  scanned_at: "2026-08-28T00:00:00Z",
};

const graph = loadGraph(DOCUMENT);

describe("loading", () => {
  it("indexes nodes and edges", () => {
    expect(graph.nodes.size).toBe(8);
    expect(graph.edges).toHaveLength(6);
  });

  it("drops edges pointing at absent nodes rather than throwing", () => {
    const partial = loadGraph({
      nodes: { a: node("a", "Column", "A") },
      edges: [{ source: "a", target: "missing", kind: "derives_from", confidence: "resolved" }],
    });
    expect(partial.edges).toHaveLength(0);
    expect(partial.droppedEdges).toBe(1);
  });

  it("rejects a file that is not a lineage graph", () => {
    expect(() => loadGraph({ hello: "world" })).toThrow(/no 'nodes' object/);
    expect(() => loadGraph(null)).toThrow(/not a lineage graph/);
  });
});

describe("direction", () => {
  it("walks derives_from towards the thing depended on", () => {
    const result = traverse(graph, "measure", { direction: "upstream", depth: 5 });
    expect([...result.nodeIds].sort()).toEqual(["col", "measure", "src"]);
  });

  it("walks used_in towards the consumer", () => {
    const result = traverse(graph, "measure", { direction: "downstream", depth: 5 });
    expect([...result.nodeIds]).toContain("visual");
    expect([...result.nodeIds]).toContain("derived");
  });

  it("reaches a visual from the source column", () => {
    const result = traverse(graph, "src", { direction: "downstream", depth: 8 });
    expect([...result.nodeIds]).toContain("visual");
  });

  it("both directions is the union", () => {
    const both = traverse(graph, "measure", { direction: "both", depth: 5 });
    expect(both.nodeIds.size).toBeGreaterThan(
      traverse(graph, "measure", { direction: "upstream", depth: 5 }).nodeIds.size
    );
  });
});

describe("confidence", () => {
  it("takes the weakest link on a path", () => {
    // measure -> col is resolved, but col -> src is a heuristic M rename
    const result = traverse(graph, "src", { direction: "downstream", depth: 8 });
    expect(result.confidence.col).toBe("heuristic");
    expect(result.confidence.measure).toBe("heuristic");
    expect(result.confidence.visual).toBe("heuristic");
  });

  it("keeps a fully resolved path resolved", () => {
    const result = traverse(graph, "col", { direction: "downstream", depth: 8 });
    expect(result.confidence.measure).toBe("resolved");
    expect(result.confidence.visual).toBe("resolved");
  });

  it("prunes edges below the floor", () => {
    const everything = traverse(graph, "measure", { direction: "upstream", depth: 5 });
    const strict = traverse(graph, "measure", {
      direction: "upstream",
      depth: 5,
      minConfidence: "resolved",
    });
    expect([...everything.nodeIds]).toContain("src");
    expect([...strict.nodeIds]).not.toContain("src");
  });

  it("weakest() ranks the tiers", () => {
    expect(weakest("resolved", "opaque")).toBe("opaque");
    expect(weakest("heuristic", "resolved")).toBe("heuristic");
    expect(weakest("resolved", "resolved")).toBe("resolved");
  });
});

describe("traversal limits", () => {
  it("respects depth", () => {
    const shallow = traverse(graph, "src", { direction: "downstream", depth: 1 });
    const deep = traverse(graph, "src", { direction: "downstream", depth: 8 });
    expect(shallow.nodeIds.size).toBeLessThan(deep.nodeIds.size);
  });

  it("records hop distance", () => {
    const result = traverse(graph, "src", { direction: "downstream", depth: 8 });
    expect(result.depths.src).toBe(0);
    expect(result.depths.col).toBe(1);
    expect(result.depths.measure).toBe(2);
  });

  it("excludes containment unless asked", () => {
    const without = traverse(graph, "col", { direction: "both", depth: 2 });
    const with_ = traverse(graph, "col", {
      direction: "both",
      depth: 2,
      includeContainment: true,
    });
    expect([...without.nodeIds]).not.toContain("table");
    expect([...with_.nodeIds]).toContain("table");
  });

  it("marks a result truncated at the node cap", () => {
    const result = traverse(graph, "src", { direction: "downstream", depth: 8, maxNodes: 2 });
    expect(result.truncated).toBe(true);
  });

  it("returns nothing for an unknown root", () => {
    expect(traverse(graph, "nope").nodeIds.size).toBe(0);
  });

  it("terminates on a cycle", () => {
    const cyclic = loadGraph({
      nodes: { a: node("a", "Measure", "A"), b: node("b", "Measure", "B") },
      edges: [
        { source: "a", target: "b", kind: "derives_from", confidence: "resolved" },
        { source: "b", target: "a", kind: "derives_from", confidence: "resolved" },
      ],
    });
    expect(traverse(cyclic, "a", { direction: "both", depth: 10 }).nodeIds.size).toBe(2);
  });
});

describe("expand", () => {
  it("returns only edges touching the node", () => {
    const result = neighbours(graph, "col");
    expect(result.edges.every((e) => e.source === "col" || e.target === "col")).toBe(true);
  });

  it("can exclude containment", () => {
    expect(neighbours(graph, "col", false).edges.some((e) => e.kind === "contains")).toBe(false);
  });

  it("handles an isolated node", () => {
    const result = neighbours(graph, "orphan");
    expect(result.edges).toHaveLength(0);
    expect(result.nodes.map((n) => n.id)).toEqual(["orphan"]);
  });
});

describe("search", () => {
  it("ranks an exact match first", () => {
    expect(search(graph, "Amount")[0].name).toBe("Amount");
  });

  it("matches a substring", () => {
    expect(search(graph, "revenue").map((n) => n.name)).toContain("Total Revenue");
  });

  it("filters by kind", () => {
    expect(search(graph, "", "Visual").every((n) => n.kind === "Visual")).toBe(true);
  });

  it("matches on the qualified name", () => {
    expect(search(graph, "Demo /").length).toBeGreaterThan(0);
  });

  it("honours the limit", () => {
    expect(search(graph, "", "", 2)).toHaveLength(2);
  });

  it("returns nothing for a miss", () => {
    expect(search(graph, "zzzz")).toHaveLength(0);
  });
});

describe("impact", () => {
  it("counts downstream objects by kind", () => {
    const result = impact(graph, "src", 8);
    expect(result.by_kind.Visual).toBe(1);
    expect(result.by_kind.Measure).toBe(2);
    expect(result.total_downstream).toBe(4);
  });

  it("reports the weakest confidence per affected object", () => {
    const result = impact(graph, "src", 8);
    expect(result.by_confidence.heuristic).toBeGreaterThan(0);
    expect(result.affected.every((item) => item.confidence === "heuristic")).toBe(true);
  });

  it("lists reports, visuals and measures, nearest first", () => {
    const result = impact(graph, "src", 8);
    const depths = result.affected.map((item) => item.depth);
    expect([...depths]).toEqual([...depths].sort((a, b) => a - b));
  });

  it("says nothing is downstream of a leaf", () => {
    expect(impact(graph, "visual", 8).total_downstream).toBe(0);
  });
});

describe("stats and counts", () => {
  it("summarises nodes and lineage confidence", () => {
    const summary = stats(graph);
    expect(summary.nodes).toBe(8);
    expect(summary.nodes_by_kind.Measure).toBe(2);
    // containment is not lineage
    expect(summary.lineage_edges_by_confidence.resolved).toBe(3);
    expect(summary.lineage_edges_by_confidence.heuristic).toBe(1);
  });

  it("counts a node's lineage neighbours in each direction", () => {
    expect(countNeighbours(graph, "measure")).toEqual({
      upstreamCount: 1,
      downstreamCount: 2,
    });
  });
});
