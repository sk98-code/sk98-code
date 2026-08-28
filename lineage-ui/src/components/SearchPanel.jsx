import { useEffect, useState } from "react";
import { search } from "../api";
import { kindStyle, NODE_KINDS } from "../theme";

const SEARCHABLE = ["Column", "Measure", "Table", "Report", "Visual", "DataSource", "Dataflow"];

export default function SearchPanel({ onPick, activeId }) {
  const [term, setTerm] = useState("");
  const [kind, setKind] = useState("");
  const [results, setResults] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    // debounce so typing does not fire a request per keystroke
    const timer = setTimeout(() => {
      search(term, kind)
        .then((payload) => !cancelled && setResults(payload.results))
        .catch((exc) => !cancelled && setError(exc.message));
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [term, kind]);

  return (
    <div className="search-panel">
      <input
        type="search"
        value={term}
        placeholder="Search columns, measures, reports…"
        onChange={(event) => setTerm(event.target.value)}
        aria-label="Search the lineage graph"
      />
      <select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Filter by kind">
        <option value="">All kinds</option>
        {SEARCHABLE.map((option) => (
          <option key={option} value={option}>
            {NODE_KINDS[option]?.label || option}
          </option>
        ))}
      </select>

      {error && <p className="detail-error">{error}</p>}
      <ul className="search-results">
        {results.map((node) => (
          <li key={node.id}>
            <button
              type="button"
              className={node.id === activeId ? "active" : ""}
              onClick={() => onPick(node.id)}
            >
              <span style={{ color: kindStyle(node.kind).color }}>
                {kindStyle(node.kind).icon}
              </span>
              <span className="search-result-name">{node.name}</span>
              <span className="search-result-path">{node.qualified_name}</span>
            </button>
          </li>
        ))}
        {results.length === 0 && <li className="detail-muted">No matches.</li>}
      </ul>
    </div>
  );
}
