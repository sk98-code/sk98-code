// Layered left-to-right layout: upstream on the left, the focus node in the
// middle, downstream on the right. Depth comes from the API's traversal
// metadata, so the columns line up with actual lineage distance rather than
// with whatever order the nodes arrived in.
//
// Done by hand rather than with a layout library: the graphs here are narrow
// and deep, one pass over the nodes is enough, and it keeps the bundle small.

const COLUMN_WIDTH = 300;
const ROW_HEIGHT = 74;

export function layoutNodes(nodes, edges, rootId, depths = {}) {
  const signed = signedDepths(nodes, edges, rootId, depths);

  const byColumn = new Map();
  for (const node of nodes) {
    const column = signed.get(node.id) ?? 0;
    if (!byColumn.has(column)) byColumn.set(column, []);
    byColumn.get(column).push(node);
  }

  const positions = new Map();
  const tallest = Math.max(...[...byColumn.values()].map((c) => c.length), 1);
  for (const [column, members] of byColumn) {
    members.sort((a, b) => (a.qualified_name || a.name).localeCompare(b.qualified_name || b.name));
    const offset = ((tallest - members.length) * ROW_HEIGHT) / 2;
    members.forEach((member, index) => {
      positions.set(member.id, {
        x: column * COLUMN_WIDTH,
        y: offset + index * ROW_HEIGHT,
      });
    });
  }
  return positions;
}

// The API reports hop distance as a positive number in both directions, so
// work out which side of the root each node sits on before laying it out.
function signedDepths(nodes, edges, rootId, depths) {
  const upstreamOf = new Set();
  const downstreamOf = new Set();

  const flow = edges.map((edge) =>
    edge.kind === "derives_from"
      ? { from: edge.target, to: edge.source }
      : { from: edge.source, to: edge.target }
  );

  // walk outwards from the root in each direction
  const walk = (start, pick, into) => {
    const queue = [start];
    const seen = new Set([start]);
    while (queue.length) {
      const current = queue.shift();
      for (const edge of flow) {
        const next = pick(edge, current);
        if (next && !seen.has(next)) {
          seen.add(next);
          into.add(next);
          queue.push(next);
        }
      }
    }
  };

  walk(rootId, (edge, current) => (edge.to === current ? edge.from : null), upstreamOf);
  walk(rootId, (edge, current) => (edge.from === current ? edge.to : null), downstreamOf);

  const signed = new Map();
  for (const node of nodes) {
    const distance = depths[node.id] ?? 0;
    if (node.id === rootId) signed.set(node.id, 0);
    else if (upstreamOf.has(node.id)) signed.set(node.id, -distance || -1);
    else if (downstreamOf.has(node.id)) signed.set(node.id, distance || 1);
    else signed.set(node.id, distance);
  }
  return signed;
}
