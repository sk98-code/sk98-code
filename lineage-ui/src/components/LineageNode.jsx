import { Handle, Position } from "reactflow";
import { kindStyle, confidenceStyle } from "../theme";

// One node in the graph. The chevron says whether its neighbours are already
// on the canvas, so a short trail reads as "collapsed" rather than "the end".
export default function LineageNode({ data, selected }) {
  const style = kindStyle(data.kind);
  const confidence = data.confidence ? confidenceStyle(data.confidence) : null;

  return (
    <div
      className={`lineage-node${selected ? " selected" : ""}${data.isRoot ? " root" : ""}`}
      style={{ borderLeftColor: style.color }}
      title={data.qualified_name || data.name}
    >
      <Handle type="target" position={Position.Left} />
      <div className="lineage-node-head">
        <span className="lineage-node-icon" style={{ color: style.color }}>
          {style.icon}
        </span>
        <span className="lineage-node-name">{data.name || "(unnamed)"}</span>
        <span
          className="lineage-node-more"
          title={
            data.expanded
              ? "neighbours shown — double-click to collapse"
              : "double-click to expand this node's neighbours"
          }
        >
          {data.expanded ? "\u2212" : "+"}
        </span>
      </div>
      <div className="lineage-node-sub">
        <span>{style.label}</span>
        {data.table && <span className="lineage-node-table">{data.table}</span>}
        {confidence && (
          <span
            className="lineage-node-confidence"
            style={{ color: confidence.color }}
            title={confidence.hint}
          >
            {confidence.label.toLowerCase()}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
