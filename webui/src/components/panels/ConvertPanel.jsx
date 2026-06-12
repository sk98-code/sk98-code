import { useState } from "react";
import { api } from "../../api.js";
import { DecisionDetails, Spin, Stat } from "../shared.jsx";

export default function ConvertPanel({ discovery, conversion, setConversion }) {
  const [status, setStatus] = useState(null);

  const convert = async () => {
    if (!discovery) {
      setStatus(<span className="err">run discovery first (step 2)</span>);
      return;
    }
    setStatus(<Spin />);
    try {
      setConversion(await api("/api/convert-estate", { method: "POST" }));
      setStatus(null);
    } catch (e) {
      setStatus(<span className="err">{e.message}</span>);
    }
  };

  const tiers = { AUTO: 0, ASSISTED: 0, MANUAL: 0 };
  conversion?.decisions.forEach((d) => tiers[d.tier]++);
  const total = Math.max(conversion?.decisions.length || 0, 1);

  return (
    <>
      <div className="controls">
        <button className="btn" onClick={convert}>
          Convert estate → PBIP
        </button>
        <span>{status}</span>
      </div>
      {!conversion && (
        <div className="empty">
          Run discovery first; conversion emits PBIP projects with TMDL semantic models, DAX
          measures, M partitions and RLS proposals — every expression decision is confidence-tiered.
        </div>
      )}
      {conversion && (
        <div className="card">
          <h3>Conversion result</h3>
          <Stat value={conversion.projects.length} label="PBIP projects" />
          <Stat value={tiers.AUTO} label="AUTO" color="#54d98a" />
          <Stat value={tiers.ASSISTED} label="ASSISTED" color="#ffd45e" />
          <Stat value={tiers.MANUAL} label="MANUAL" color="#ff8b94" />
          <a href={conversion.download} className="pill" style={{ marginLeft: 14 }}>
            ⬇ download PBIP bundle (.zip)
          </a>
          <div className="bar">
            <i style={{ width: `${(100 * tiers.AUTO) / total}%`, background: "var(--auto)" }} />
            <i style={{ width: `${(100 * tiers.ASSISTED) / total}%`, background: "var(--assisted)" }} />
            <i style={{ width: `${(100 * tiers.MANUAL) / total}%`, background: "var(--manual)" }} />
          </div>
          <div style={{ marginTop: 12 }}>
            {conversion.decisions.map((d, i) => (
              <DecisionDetails key={i} decision={d} />
            ))}
          </div>
        </div>
      )}
    </>
  );
}
