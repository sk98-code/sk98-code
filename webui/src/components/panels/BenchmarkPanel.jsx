import { useState } from "react";
import { api } from "../../api.js";
import { Annot, Spin, Stat, TierPill } from "../shared.jsx";

export default function BenchmarkPanel() {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setResult(await api("/api/benchmark", { method: "POST" }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>
        Conversion-accuracy benchmark{" "}
        <span className="muted">(golden corpus · per tier × band, never blended)</span>
      </h3>
      <div className="controls">
        <button className="btn" onClick={run} disabled={busy}>
          Run benchmark
        </button>
        {busy && <Spin />}
      </div>
      {result && (
        <>
          <Stat value={`${result.passed}/${result.total}`} label="cases passed" />
          <Stat value={`${result.automated_conversion_rate}%`} label="automated rate" />
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Expected tier</th><th>Band</th><th>Total</th><th>Passed</th><th>Accuracy</th>
              </tr>
            </thead>
            <tbody>
              {result.accuracy_matrix.map((r, i) => (
                <tr key={i}>
                  <td><TierPill tier={r.tier} /></td>
                  <td className={`band-${r.complexity_band}`}>{r.complexity_band}</td>
                  <td>{r.total}</td>
                  <td>{r.passed}</td>
                  <td>{r.accuracy_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.failures.length > 0 && (
            <div className="card" style={{ marginTop: 10 }}>
              <h3 className="err">Failures</h3>
              {result.failures.map((f, i) => (
                <Annot key={i}>
                  <code>{f.expression}</code> expected {f.expected_tier}, got {f.actual_tier}
                </Annot>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
