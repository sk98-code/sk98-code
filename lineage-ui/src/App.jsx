import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  MarkerType,
  useEdgesState,
  useNodesState,
} from "reactflow";
import "reactflow/dist/style.css";

import { autoDetectClient, loadBundledGraph, loadGraphFile } from "./graph/client";
import { ClientProvider } from "./graph/ClientContext";
import LineageNodeCard from "./components/LineageNode";
import DetailPanel from "./components/DetailPanel";
import SearchPanel from "./components/SearchPanel";
import SourceBar from "./components/SourceBar";
import { layoutNodes } from "./layout";
import { CONFIDENCE, confidenceStyle, kindStyle } from "./theme";

const nodeTypes = { lineage: LineageNodeCard };

// A single expansion that would drop hundreds of nodes onto the canvas helps
// nobody; cap it and say so rather than freezing the view.
const EXPAND_LIMIT = 25;

// Vite rewrites this for the hosted build, where the app is served from a
// subpath rather than the domain root.
const DEMO_URL = `${import.meta.env.BASE_URL}demo-graph.json`;

// Injected by vite.config: true for the GitHub Pages bundle, which has no
// server to talk to. Declared here so the reference is obvious.
/* global __STATIC_BUILD__ */

export default function App() {
  const [client, setClient] = useState(null);
  const [booting, setBooting] = useState(true);
  const [rootId, setRootId] = useState("");
  const [direction, setDirection] = useState("both");
  const [depth, setDepth] = useState(3);
  const [minConfidence, setMinConfidence] = useState("");
  const [showContainment, setShowContainment] = useState(false);

  const [base, setBase] = useState({ nodes: [], edges: [], meta: {} });
  const [expansions, setExpansions] = useState({});
  const [selected, setSelected] = useState(null);
  const [stats, setStats] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [showWarnings, setShowWarnings] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState([]);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState([]);

  // -- choosing a data source ----------------------------------------------
  const adopt = useCallback((next) => {
    setClient(next);
    setRootId("");
    setBase({ nodes: [], edges: [], meta: {} });
    setExpansions({});
    setSelected(null);
    setError("");
    setNotice("");
  }, []);

  useEffect(() => {
    let cancelled = false;
    autoDetectClient(DEMO_URL, { preferApi: !__STATIC_BUILD__ })
      .then((detected) => !cancelled && adopt(detected))
      .catch((exc) => !cancelled && setError(exc.message))
      .finally(() => !cancelled && setBooting(false));
    return () => {
      cancelled = true;
    };
  }, [adopt]);

  const openFile = useCallback(
    async (file) => {
      setBooting(true);
      try {
        adopt(await loadGraphFile(file));
      } catch (exc) {
        setError(exc.message);
      } finally {
        setBooting(false);
      }
    },
    [adopt]
  );

  const openDemo = useCallback(async () => {
    setBooting(true);
    try {
      adopt(await loadBundledGraph(DEMO_URL, "Demo tenant"));
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBooting(false);
    }
  }, [adopt]);

  // -- graph data -----------------------------------------------------------
  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    client
      .stats()
      .then((result) => !cancelled && setStats(result))
      .catch((exc) => !cancelled && setError(exc.message));
    client
      .warnings()
      .then((payload) => !cancelled && setWarnings(payload.warnings || []))
      .catch(() => !cancelled && setWarnings([]));
    return () => {
      cancelled = true;
    };
  }, [client]);

  // Reload the base subgraph whenever the focus or the controls change.
  useEffect(() => {
    if (!client || !rootId) return undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    client
      .lineage(rootId, direction, depth, minConfidence)
      .then((payload) => {
        if (cancelled) return;
        setBase(payload);
        setExpansions({});
        if (payload.truncated) {
          setNotice("This view was truncated — narrow the depth or filter by confidence.");
        }
      })
      .catch((exc) => !cancelled && setError(exc.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client, rootId, direction, depth, minConfidence]);

  useEffect(() => {
    if (!client || !rootId) return;
    client
      .node(rootId)
      .then((payload) => setSelected(payload.node))
      .catch(() => setSelected(null));
  }, [client, rootId]);

  // Merge the base subgraph with everything the user has expanded.
  const merged = useMemo(() => {
    const nodes = new Map();
    const edges = new Map();
    const absorb = (payload) => {
      for (const node of payload.nodes || []) nodes.set(node.id, node);
      for (const edge of payload.edges || []) {
        edges.set(`${edge.source}|${edge.target}|${edge.kind}`, edge);
      }
    };
    absorb(base);
    Object.values(expansions).forEach(absorb);

    const visibleEdges = [...edges.values()].filter(
      (edge) => showContainment || edge.kind !== "contains"
    );
    const referenced = new Set([rootId]);
    visibleEdges.forEach((edge) => {
      referenced.add(edge.source);
      referenced.add(edge.target);
    });
    return {
      nodes: [...nodes.values()].filter((node) => referenced.has(node.id)),
      edges: visibleEdges,
    };
  }, [base, expansions, showContainment, rootId]);

  // Turn the merged graph into what react-flow renders.
  useEffect(() => {
    const depths = base.meta?.depths || {};
    const confidence = base.meta?.confidence || {};
    const positions = layoutNodes(merged.nodes, merged.edges, rootId, depths);

    setFlowNodes(
      merged.nodes.map((node) => ({
        id: node.id,
        type: "lineage",
        position: positions.get(node.id) || { x: 0, y: 0 },
        selected: selected?.id === node.id,
        data: {
          kind: node.kind,
          name: node.name,
          qualified_name: node.qualified_name,
          table: node.properties?.table,
          confidence: confidence[node.id],
          isRoot: node.id === rootId,
          expanded: Boolean(expansions[node.id]),
        },
      }))
    );

    setFlowEdges(
      merged.edges.map((edge) => {
        const style = confidenceStyle(edge.confidence);
        const isContainment = edge.kind === "contains";
        return {
          id: `${edge.source}|${edge.target}|${edge.kind}`,
          // draw in data-flow direction: upstream on the left
          source: edge.kind === "derives_from" ? edge.target : edge.source,
          target: edge.kind === "derives_from" ? edge.source : edge.target,
          animated: false,
          label: edge.properties?.role || undefined,
          style: {
            stroke: isContainment ? "#cbd5e1" : style.color,
            strokeWidth: isContainment ? 1 : 1.8,
            strokeDasharray: isContainment ? "1 4" : style.dash,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: isContainment ? "#cbd5e1" : style.color,
          },
          data: edge,
        };
      })
    );
  }, [merged, rootId, selected?.id, base.meta, expansions, setFlowNodes, setFlowEdges]);

  const toggleExpand = useCallback(
    async (nodeId) => {
      if (expansions[nodeId]) {
        setExpansions((current) => {
          const next = { ...current };
          delete next[nodeId];
          return next;
        });
        return;
      }
      try {
        const payload = await client.expand(nodeId, showContainment);
        const known = new Set(merged.nodes.map((node) => node.id));
        const fresh = payload.nodes.filter((node) => !known.has(node.id));
        if (fresh.length > EXPAND_LIMIT) {
          const keep = new Set(fresh.slice(0, EXPAND_LIMIT).map((node) => node.id));
          keep.add(nodeId);
          known.forEach((id) => keep.add(id));
          setNotice(
            `${fresh.length} neighbours here; showing the first ${EXPAND_LIMIT}. ` +
              "Focus this node to explore the rest."
          );
          setExpansions((current) => ({
            ...current,
            [nodeId]: {
              nodes: payload.nodes.filter((node) => keep.has(node.id)),
              edges: payload.edges.filter(
                (edge) => keep.has(edge.source) && keep.has(edge.target)
              ),
            },
          }));
          return;
        }
        setExpansions((current) => ({ ...current, [nodeId]: payload }));
      } catch (exc) {
        setError(exc.message);
      }
    },
    [client, expansions, merged.nodes, showContainment]
  );

  const onNodeClick = useCallback(
    (_event, flowNode) => {
      const found = merged.nodes.find((node) => node.id === flowNode.id);
      if (found) setSelected(found);
    },
    [merged.nodes]
  );

  const onNodeDoubleClick = useCallback(
    (_event, flowNode) => toggleExpand(flowNode.id),
    [toggleExpand]
  );

  if (booting && !client) {
    return (
      <div className="app boot">
        <p>Loading lineage…</p>
        {error && <p className="detail-error">{error}</p>}
      </div>
    );
  }

  return (
    <ClientProvider client={client}>
      <div className="app">
        <header className="app-header">
          <div className="app-title">
            <h1>Power BI column lineage</h1>
            <p>
              source column → Power Query → semantic model → report visual
              {stats ? ` · ${stats.nodes} nodes` : ""}
            </p>
          </div>
          <div className="legend">
            {Object.entries(CONFIDENCE).map(([key, value]) => (
              <span key={key} className="legend-item" title={value.hint}>
                <svg width="26" height="10" aria-hidden="true">
                  <line
                    x1="0"
                    y1="5"
                    x2="26"
                    y2="5"
                    stroke={value.color}
                    strokeWidth="2"
                    strokeDasharray={value.dash}
                  />
                </svg>
                {value.label}
              </span>
            ))}
            {warnings.length > 0 && (
              <button
                type="button"
                className="legend-warnings"
                onClick={() => setShowWarnings((current) => !current)}
              >
                {warnings.length} scan warning{warnings.length === 1 ? "" : "s"}
              </button>
            )}
          </div>
        </header>

        <SourceBar
          client={client}
          onLoadFile={openFile}
          onLoadDemo={openDemo}
          busy={booting}
        />

        {showWarnings && (
          <div className="warnings-drawer">
            <h3>Where the scan could not see clearly</h3>
            <ul>
              {warnings.slice(0, 100).map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="app-body">
          <nav className="sidebar">
            <SearchPanel onPick={setRootId} activeId={rootId} />
          </nav>

          <main className="canvas">
            <div className="canvas-controls">
              <label>
                Direction
                <select value={direction} onChange={(event) => setDirection(event.target.value)}>
                  <option value="both">Both</option>
                  <option value="upstream">Upstream</option>
                  <option value="downstream">Downstream</option>
                </select>
              </label>
              <label>
                Depth
                <input
                  type="range"
                  min="1"
                  max="8"
                  value={depth}
                  onChange={(event) => setDepth(Number(event.target.value))}
                />
                <span className="control-value">{depth}</span>
              </label>
              <label>
                Confidence
                <select
                  value={minConfidence}
                  onChange={(event) => setMinConfidence(event.target.value)}
                >
                  <option value="">Everything</option>
                  <option value="heuristic">Heuristic and better</option>
                  <option value="resolved">Resolved only</option>
                </select>
              </label>
              <label className="control-checkbox">
                <input
                  type="checkbox"
                  checked={showContainment}
                  onChange={(event) => setShowContainment(event.target.checked)}
                />
                Show containment
              </label>
              {loading && <span className="control-status">loading…</span>}
            </div>

            {error && <div className="banner banner-error">{error}</div>}
            {notice && (
              <div className="banner banner-notice">
                {notice}
                <button type="button" onClick={() => setNotice("")}>
                  dismiss
                </button>
              </div>
            )}

            {!rootId && <EmptyState mode={client?.mode} />}

            {rootId && (
              <ReactFlow
                nodes={flowNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={onNodeClick}
                onNodeDoubleClick={onNodeDoubleClick}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                minZoom={0.15}
              >
                <Background gap={20} color="#e2e8f0" />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable nodeColor={(node) => kindStyle(node.data?.kind).color} />
              </ReactFlow>
            )}
          </main>

          {selected && (
            <DetailPanel node={selected} onFocus={setRootId} onClose={() => setSelected(null)} />
          )}
        </div>
      </div>
    </ClientProvider>
  );
}

function EmptyState({ mode }) {
  return (
    <div className="empty-state">
      <h2>Pick a column, measure or report to trace</h2>
      <p>
        Search on the left. Click a node for its details and downstream impact; double-click
        to expand or collapse its neighbours.
      </p>
      <ul className="empty-legend">
        {["DataSource", "Column", "Measure", "Visual"].map((kind) => (
          <li key={kind}>
            <span style={{ color: kindStyle(kind).color }}>{kindStyle(kind).icon}</span>{" "}
            {kindStyle(kind).label}
          </li>
        ))}
      </ul>

      {mode === "local" && (
        <div className="empty-howto">
          <h3>Tracing your own tenant</h3>
          <p>
            Scanning needs a service principal and, for Premium workspaces, an XMLA
            connection — so it runs on your machine, not on this page. This page never
            sees your credentials.
          </p>
          <pre>
            {`pip install "pbilineage[api] @ git+https://github.com/sk98-code/sk98-code"
cp .env.example .env      # tenant id, client id, secret
pbilineage doctor         # check permissions
pbilineage scan           # writes out/lineage/graph.json`}
          </pre>
          <p>
            Then choose <strong>Open graph.json</strong> above, or drop the file anywhere
            on that bar. It is read locally and never uploaded.
          </p>
        </div>
      )}
    </div>
  );
}
