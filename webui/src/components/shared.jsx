export const TierPill = ({ tier }) => <span className={`tier ${tier}`}>{tier}</span>;

export const Spin = () => <span className="spin" />;

export const Stat = ({ value, label, color }) => (
  <span className="stat">
    <b style={color ? { color } : undefined}>{value}</b>
    <span>{label}</span>
  </span>
);

export const Annot = ({ children }) => <div className="annot">{children}</div>;

export function DecisionDetails({ decision }) {
  return (
    <details>
      <summary>
        <TierPill tier={decision.tier} /> <code>{decision.source_expression.slice(0, 90)}</code>{" "}
        <span className="muted">({decision.source_artifact})</span>
      </summary>
      {decision.target_expression ? (
        <pre>{decision.target_expression}</pre>
      ) : (
        <pre className="err">{decision.fallback_strategy || "manual"}</pre>
      )}
      <div className="muted">
        confidence {decision.confidence} · rule: {decision.rule_id || "—"}
      </div>
      {decision.annotations.map((a, i) => (
        <Annot key={i}>{a}</Annot>
      ))}
    </details>
  );
}
