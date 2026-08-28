import { useEffect, useState } from "react";
import { getImpact } from "../api";
import { confidenceStyle, kindStyle } from "../theme";

// The side panel: what this object is, the expression behind it, and what
// breaks downstream if it changes.
export default function DetailPanel({ node, onFocus, onClose }) {
  const [impact, setImpact] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!node) return undefined;
    let cancelled = false;
    setImpact(null);
    setError("");
    setLoading(true);
    getImpact(node.id)
      .then((result) => !cancelled && setImpact(result))
      .catch((exc) => !cancelled && setError(exc.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [node?.id]);

  if (!node) return null;
  const style = kindStyle(node.kind);
  const properties = node.properties || {};
  const expression = properties.expression;

  return (
    <aside className="detail-panel">
      <header>
        <div>
          <span className="detail-kind" style={{ color: style.color }}>
            {style.icon} {style.label}
          </span>
          <h2>{node.name}</h2>
          <p className="detail-path">{node.qualified_name}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </header>

      {expression && (
        <section>
          <h3>Expression</h3>
          <pre className="detail-expression">{expression}</pre>
        </section>
      )}

      <section>
        <h3>Properties</h3>
        <dl className="detail-properties">
          {Object.entries(properties)
            .filter(([key, value]) => key !== "expression" && value !== "" && value != null)
            .map(([key, value]) => (
              <div key={key}>
                <dt>{key.replace(/_/g, " ")}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
        </dl>
      </section>

      <section>
        <h3>Downstream impact</h3>
        {loading && <p className="detail-muted">calculating…</p>}
        {error && <p className="detail-error">{error}</p>}
        {impact && (
          <>
            <p className="detail-muted">
              {impact.total_downstream} object(s) depend on this.
            </p>
            <div className="detail-chips">
              {Object.entries(impact.by_confidence).map(([confidence, count]) => (
                <span
                  key={confidence}
                  className="detail-chip"
                  style={{ borderColor: confidenceStyle(confidence).color }}
                  title={confidenceStyle(confidence).hint}
                >
                  {confidence} {count}
                </span>
              ))}
            </div>
            <ul className="detail-affected">
              {impact.affected.slice(0, 40).map((item) => (
                <li key={item.id}>
                  <button type="button" onClick={() => onFocus(item.id)}>
                    <span style={{ color: kindStyle(item.kind).color }}>
                      {kindStyle(item.kind).icon}
                    </span>{" "}
                    {item.qualified_name || item.name}
                  </button>
                  <span
                    className="detail-affected-confidence"
                    style={{ color: confidenceStyle(item.confidence).color }}
                  >
                    {item.confidence}
                  </span>
                </li>
              ))}
            </ul>
            {impact.affected.length === 0 && (
              <p className="detail-muted">Nothing downstream consumes this yet.</p>
            )}
          </>
        )}
      </section>
    </aside>
  );
}
