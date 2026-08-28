import { useRef, useState } from "react";

// Where the graph on screen came from, and how to swap it.
//
// Loading is deliberately local: the file is read with the File API and
// stays in this tab. Nothing is uploaded, which is what makes it reasonable
// to explore a real tenant's lineage on a page hosted by someone else.
export default function SourceBar({ client, onLoadFile, onLoadDemo, busy }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (files) => {
    const file = files?.[0];
    if (file) onLoadFile(file);
  };

  return (
    <div
      className={`source-bar${dragging ? " dragging" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        handleFiles(event.dataTransfer.files);
      }}
    >
      <span className="source-label">
        <span className={`source-dot source-dot-${client?.mode || "none"}`} aria-hidden="true" />
        {client?.mode === "api" ? "Live API" : client?.label || "No graph"}
      </span>

      <input
        ref={input}
        type="file"
        accept="application/json,.json"
        hidden
        onChange={(event) => {
          handleFiles(event.target.files);
          event.target.value = ""; // let the same file be re-picked
        }}
      />
      <button type="button" onClick={() => input.current?.click()} disabled={busy}>
        Open graph.json
      </button>
      <button type="button" className="source-secondary" onClick={onLoadDemo} disabled={busy}>
        Demo tenant
      </button>
      <span className="source-privacy">Read in your browser — never uploaded</span>
    </div>
  );
}
