import { useState } from "react";
import { postJson } from "../../api.js";
import { Annot, Spin, TierPill } from "../shared.jsx";

export default function ExpressionPanel({ platform }) {
  const [expr, setExpr] = useState(platform.examples[0]);
  const [table, setTable] = useState("Sales");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const convert = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await postJson("/api/convert-expression", {
          tool: platform.key,
          expression: expr,
          table_hint: table || "Data",
        }),
      );
    } catch (e) {
      setResult(null);
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>
        Expression → DAX playground <span className="muted">· {platform.label} dialect</span>
      </h3>
      <div className="controls">
        <input
          type="text"
          value={table}
          onChange={(e) => setTable(e.target.value)}
          style={{ width: 150 }}
          title="fact table hint"
        />
        <button className="btn" onClick={convert} disabled={busy}>
          Convert to DAX
        </button>
        <span className="muted">try:</span>
        {platform.examples.map((e) => (
          <button key={e} className="pill" onClick={() => setExpr(e)}>
            {e.length > 36 ? e.slice(0, 36) + "…" : e}
          </button>
        ))}
      </div>
      <textarea rows={4} value={expr} onChange={(e) => setExpr(e.target.value)} />
      {busy && <Spin />}
      {error && <pre className="err">{error}</pre>}
      {result && !busy && (
        <>
          <div style={{ margin: "10px 0" }}>
            <TierPill tier={result.tier} />{" "}
            <span className="muted">
              confidence {result.confidence} · cited rule: <code>{result.rule_id || "—"}</code>
            </span>
          </div>
          {result.target_expression ? (
            <pre>{result.target_expression}</pre>
          ) : (
            <pre className="err">{result.fallback_strategy || "not converted"}</pre>
          )}
          {result.annotations.map((a, i) => (
            <Annot key={i}>{a}</Annot>
          ))}
          {result.cited_rule && (
            <details style={{ marginTop: 8 }}>
              <summary>ConversionRule {result.cited_rule.rule_id}</summary>
              <pre>{JSON.stringify(result.cited_rule, null, 2)}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}
