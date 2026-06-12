import { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { TierPill } from "../shared.jsx";

export default function MappingsPanel({ platform }) {
  const [rows, setRows] = useState([]);
  const [tier, setTier] = useState("");
  const [layer, setLayer] = useState("");

  useEffect(() => {
    api(`/api/mappings?tool=${platform.key}${tier ? `&tier=${tier}` : ""}`).then(setRows);
  }, [platform.key, tier]);

  const layers = useMemo(() => [...new Set(rows.map((m) => m.source_layer))].sort(), [rows]);
  const filtered = layer ? rows.filter((m) => m.source_layer === layer) : rows;

  return (
    <div className="card">
      <h3>Feature mapping matrix — {platform.label} → Power BI</h3>
      <div className="controls">
        <select value={tier} onChange={(e) => setTier(e.target.value)}>
          <option value="">all tiers</option>
          <option>AUTO</option>
          <option>ASSISTED</option>
          <option>MANUAL</option>
        </select>
        <select value={layer} onChange={(e) => setLayer(e.target.value)}>
          <option value="">all layers</option>
          {layers.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
        <span className="muted">{filtered.length} mappings</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Feature</th><th>Layer</th><th>Power BI equivalent</th><th>Tier</th><th>Fallback / notes</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((m) => (
            <tr key={m.feature_name}>
              <td>{m.feature_name}</td>
              <td>{m.source_layer}</td>
              <td>{m.powerbi_equivalent || <span className="err">(no native equivalent)</span>}</td>
              <td>
                <TierPill tier={m.tier} />
              </td>
              <td className="muted">{m.fallback_strategy || m.notes || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
