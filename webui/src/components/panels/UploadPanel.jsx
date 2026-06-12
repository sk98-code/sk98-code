import { useRef, useState } from "react";
import { api } from "../../api.js";

export default function UploadPanel({ platform, uploads, setUploads }) {
  const fileInput = useRef(null);
  const [over, setOver] = useState(false);
  const [msg, setMsg] = useState(null);

  const uploadFiles = async (files) => {
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    try {
      const res = await api("/api/upload", { method: "POST", body: fd });
      const info = await api("/api/info");
      setUploads(info.uploads.filter((n) => platform.exts.some((e) => n.toLowerCase().endsWith(e))));
      setMsg(
        <>
          {res.rejected.length > 0 && <div className="err">skipped (unsupported): {res.rejected.join(", ")}</div>}
          {res.saved.length > 0 && <div className="muted">{res.saved.length} file(s) saved — continue to Discovery →</div>}
        </>,
      );
    } catch (e) {
      setMsg(<div className="err">{e.message}</div>);
    }
  };

  return (
    <>
      <div className="card">
        <h3>Upload your {platform.label} estate</h3>
        <div
          className={"drop" + (over ? " over" : "")}
          onClick={() => fileInput.current.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            uploadFiles(e.dataTransfer.files);
          }}
        >
          Drop <b>{platform.exts.join(" / ")}</b> files here, or <b>click to browse</b>
          <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
            {platform.hint}
          </div>
        </div>
        <input
          type="file"
          ref={fileInput}
          multiple
          hidden
          accept={platform.exts.join(",")}
          onChange={(e) => uploadFiles(e.target.files)}
        />
        <div style={{ marginTop: 10 }}>
          {uploads.map((n) => (
            <span key={n} className="pill">
              {n}
            </span>
          ))}
        </div>
        {msg}
      </div>
      <div className="card">
        <h3>Privacy</h3>
        <label className="muted">
          <ScrubToggle /> Scrub mode — pseudonymize servers/principals and strip credentials, emails
          and IPs before anything is stored or reported
        </label>
      </div>
    </>
  );
}

// scrub preference shared with DiscoverPanel via sessionStorage: it is a
// per-workspace toggle, not server state
export function getScrub() {
  return sessionStorage.getItem("bimigrate.scrub") !== "off";
}

function ScrubToggle() {
  const [on, setOn] = useState(getScrub());
  return (
    <input
      type="checkbox"
      checked={on}
      onChange={(e) => {
        setOn(e.target.checked);
        sessionStorage.setItem("bimigrate.scrub", e.target.checked ? "on" : "off");
      }}
    />
  );
}
