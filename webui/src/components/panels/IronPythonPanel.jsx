import { useState } from "react";
import { postJson } from "../../api.js";
import { SAMPLE_IRONPYTHON } from "../../platforms.js";
import { Annot, TierPill } from "../shared.jsx";

export default function IronPythonPanel() {
  const [code, setCode] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const classify = async () => {
    setError(null);
    try {
      setResult(await postJson("/api/classify-script", { name: "script", code }));
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="card">
      <h3>
        IronPython triage <TierPill tier="MANUAL" />
      </h3>
      <p className="muted">
        Scripts are never auto-converted; intent is classified from Document API calls with a Power
        Automate / bookmarks / Fabric rebuild recommendation per script.
      </p>
      <div className="controls">
        <button className="btn" onClick={classify}>
          Classify
        </button>
        <button className="btn ghost" onClick={() => setCode(SAMPLE_IRONPYTHON)}>
          Load sample
        </button>
      </div>
      <textarea rows={10} value={code} onChange={(e) => setCode(e.target.value)} />
      {error && <pre className="err">{error}</pre>}
      {result && (
        <>
          <div style={{ margin: "10px 0" }}>
            <TierPill tier={result.tier} /> primary intent: <b>{result.primary_intent}</b>{" "}
            <span className="muted">
              ({result.intents.join(", ") || "none"}) · confidence {result.confidence}
            </span>
          </div>
          <pre>{result.recommendation}</pre>
          {result.api_calls.map((c, i) => (
            <Annot key={i}>
              line {c.line}: <b>{c.intent}</b> — {c.recommendation}
            </Annot>
          ))}
        </>
      )}
    </div>
  );
}
