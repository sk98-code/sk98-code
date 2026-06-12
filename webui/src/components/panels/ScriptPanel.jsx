import { useState } from "react";
import { postJson } from "../../api.js";
import { SAMPLE_LOAD_SCRIPT } from "../../platforms.js";
import { Annot, Spin } from "../shared.jsx";

export default function ScriptPanel() {
  const [script, setScript] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const convert = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await postJson("/api/convert-script", { script }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <h3>Load script → Power Query M</h3>
        <div className="controls">
          <button className="btn" onClick={convert} disabled={busy}>
            Convert script
          </button>
          <button className="btn ghost" onClick={() => setScript(SAMPLE_LOAD_SCRIPT)}>
            Load sample
          </button>
          {result && (
            <span className="muted">
              statement conversion rate: {(result.conversion_rate * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <textarea
          rows={12}
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder={"Sales:\nLOAD OrderID, Amount FROM [lib://Data/sales.qvd] (qvd) WHERE Year(OrderDate) >= 2020;"}
        />
        {busy && <Spin />}
        {error && <pre className="err">{error}</pre>}
      </div>
      {result &&
        result.queries.map((q) => (
          <div key={q.name} className="card">
            <h3>
              {q.name} <span className="muted">confidence {q.confidence}</span>
            </h3>
            <pre>{q.m_code}</pre>
            {q.annotations.map((a, i) => (
              <Annot key={i}>{a}</Annot>
            ))}
          </div>
        ))}
      {result && result.annotations.length > 0 && (
        <div className="card">
          <h3>Script-level notes</h3>
          {result.annotations.map((a, i) => (
            <Annot key={i}>{a}</Annot>
          ))}
        </div>
      )}
      {result && result.unconverted.length > 0 && (
        <div className="card">
          <h3 className="err">Unconverted blocks</h3>
          {result.unconverted.map((u, i) => (
            <Annot key={i}>
              <code>{u.text}</code> — {u.reason}
            </Annot>
          ))}
        </div>
      )}
    </>
  );
}
