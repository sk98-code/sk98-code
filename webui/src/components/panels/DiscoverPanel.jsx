import { useState } from "react";
import { api } from "../../api.js";
import { Annot, Spin, Stat } from "../shared.jsx";
import { getScrub } from "./UploadPanel.jsx";

export default function DiscoverPanel({ platform, discovery, setDiscovery, setUploads, setConversion }) {
  const [status, setStatus] = useState(null);

  const runDiscovery = async () => {
    setStatus(<Spin />);
    try {
      const d = await api(`/api/discover?scrub=${getScrub()}&tool=${platform.key}`, { method: "POST" });
      setDiscovery(d);
      setStatus(null);
    } catch (e) {
      setStatus(<span className="err">{e.message}</span>);
    }
  };

  const reset = async () => {
    await api("/api/reset", { method: "POST" });
    setDiscovery(null);
    setConversion(null);
    setUploads([]);
    setStatus(<span className="muted">workspace cleared</span>);
  };

  const bands = { simple: 0, medium: 0, complex: 0 };
  discovery?.artifacts.forEach((a) => bands[a.band]++);

  return (
    <>
      <div className="controls">
        <button className="btn" onClick={runDiscovery}>
          Run discovery
        </button>
        <button className="btn ghost" onClick={reset}>
          Reset workspace
        </button>
        <span>{status}</span>
      </div>
      {!discovery && (
        <div className="empty">
          Upload files in step 1, then run discovery to inventory the estate — complexity scoring,
          feature flags, near-duplicate clusters and effort estimates.
        </div>
      )}
      {discovery && (
        <>
          <div className="row">
            <div className="card">
              <h3>Estate summary</h3>
              <Stat value={discovery.artifacts.length} label="artifacts" />
              <Stat value={discovery.artifacts.reduce((s, a) => s + a.visuals, 0)} label="visuals" />
              <Stat value={discovery.artifacts.reduce((s, a) => s + a.calculations, 0)} label="calculations" />
              <Stat value={`${discovery.savings.total_effort_hours}h`} label="est. effort" />
              <Stat value={bands.simple} label="simple" color="#54d98a" />
              <Stat value={bands.medium} label="medium" color="#ffd45e" />
              <Stat value={bands.complex} label="complex" color="#ff8b94" />
              {discovery.errors.map((e) => (
                <div key={e.file} className="err">
                  {e.file}: {e.error}
                </div>
              ))}
            </div>
            <div className="card">
              <h3>
                Near-duplicate clusters <span className="muted">(migrate-one, retire-many)</span>
              </h3>
              {discovery.clusters.length === 0 && <div className="muted">no near-duplicates found</div>}
              {discovery.clusters.map((c, i) => (
                <div key={i}>
                  migrate <b>{c.representative}</b> → retire {c.retire.join(", ")}
                </div>
              ))}
              {discovery.clusters.length > 0 && (
                <div className="muted" style={{ marginTop: 6 }}>
                  dedup saves ~{discovery.savings.saved_effort_hours}h ({discovery.savings.savings_pct}%)
                </div>
              )}
            </div>
          </div>
          <div className="card">
            <h3>Artifacts by complexity</h3>
            <table>
              <thead>
                <tr>
                  <th>Artifact</th><th>Band</th><th>Score</th><th>Effort h</th>
                  <th>Visuals</th><th>Calcs</th><th>Scripts</th><th>Security</th><th></th>
                </tr>
              </thead>
              <tbody>
                {discovery.artifacts.map((a) => (
                  <tr key={a.name}>
                    <td>{a.name}</td>
                    <td className={`band-${a.band}`}>{a.band}</td>
                    <td>{a.score.toFixed(1)}</td>
                    <td>{a.effort_hours}</td>
                    <td>{a.visuals}</td>
                    <td>{a.calculations}</td>
                    <td>{a.scripts}</td>
                    <td>{a.security}</td>
                    <td>
                      <details>
                        <summary>flags</summary>
                        {Object.entries(a.feature_flags).map(([k, v]) => (
                          <div key={k} className="flag">
                            {k}: {v}
                          </div>
                        ))}
                        {a.issues.map((x, i) => (
                          <Annot key={i}>{x}</Annot>
                        ))}
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
