import { PLATFORMS } from "../platforms.js";

export default function PlatformSelect({ onSelect }) {
  return (
    <section className="view-select">
      <div className="brand">
        <div className="logo">b</div>
        <div>
          <b>bimigrate</b>
          <br />
          <span>Enterprise BI Migration Platform</span>
        </div>
      </div>
      <div className="hero">
        <h1>
          Where are you migrating <em>from</em>?
        </h1>
        <p>
          Select your source platform. The workspace adapts to its feature surface — calculations,
          scripts, security and scheduling — and converts everything it can to Power BI / Microsoft
          Fabric with confidence-tiered decisions.
        </p>
      </div>
      <div className="plat-grid">
        {Object.values(PLATFORMS).map((p) => (
          <button key={p.key} className="plat" style={{ "--c": p.color }} onClick={() => onSelect(p.key)}>
            <div className="mono-badge">{p.mono}</div>
            <h2>{p.label}</h2>
            <div className="vendor">{p.vendor}</div>
            <div className="exts">
              {p.exts.map((e) => (
                <span key={e} className="pill">
                  {e}
                </span>
              ))}
            </div>
            <ul>
              {p.feats.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
            <span className="go">Start migration</span>
          </button>
        ))}
      </div>
      <div className="target-note">
        <span>Migration target:</span> <span className="pbi">PB</span>
        <b>Power BI / Microsoft Fabric</b>
        <span className="muted">
          · PBIP projects · TMDL semantic models · DAX · Power Query M · RLS
        </span>
      </div>
    </section>
  );
}
