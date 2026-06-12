import { useState } from "react";
import { STEP_META } from "../platforms.js";
import UploadPanel from "./panels/UploadPanel.jsx";
import DiscoverPanel from "./panels/DiscoverPanel.jsx";
import MappingsPanel from "./panels/MappingsPanel.jsx";
import ExpressionPanel from "./panels/ExpressionPanel.jsx";
import ScriptPanel from "./panels/ScriptPanel.jsx";
import IronPythonPanel from "./panels/IronPythonPanel.jsx";
import ConvertPanel from "./panels/ConvertPanel.jsx";
import BenchmarkPanel from "./panels/BenchmarkPanel.jsx";

const PANELS = {
  upload: UploadPanel,
  discover: DiscoverPanel,
  mappings: MappingsPanel,
  expression: ExpressionPanel,
  script: ScriptPanel,
  ironpython: IronPythonPanel,
  convert: ConvertPanel,
  benchmark: BenchmarkPanel,
};

export default function Workspace({ platform, info, onLeave }) {
  const steps = platform.steps;
  const [stepIdx, setStepIdx] = useState(0);
  const [onBenchmark, setOnBenchmark] = useState(false);
  // estate state is lifted here so it survives step navigation
  const [uploads, setUploads] = useState([]);
  const [discovery, setDiscovery] = useState(null);
  const [conversion, setConversion] = useState(null);

  const stepKey = onBenchmark ? "benchmark" : steps[stepIdx];
  const Panel = PANELS[stepKey];

  const goto = (i) => {
    setOnBenchmark(false);
    setStepIdx(i);
  };

  return (
    <section className="view-work">
      <aside>
        <div className="ws-plat">
          <div className="mono-badge">{platform.mono}</div>
          <div>
            <b>{platform.label}</b>
            <span className="to">→ Power BI / Fabric</span>
          </div>
        </div>
        <button className="change" onClick={onLeave}>
          ← change source platform
        </button>
        <div className="nav-label">Migration steps</div>
        {steps.map((s, i) => (
          <button
            key={s}
            className={
              "step-btn" +
              (!onBenchmark && i === stepIdx ? " active" : "") +
              (i < stepIdx && !onBenchmark ? " done" : "")
            }
            onClick={() => goto(i)}
          >
            <span className="n">{i + 1}</span>
            {STEP_META[s].nav}
          </button>
        ))}
        <div className="nav-label">Quality</div>
        <button className={"step-btn" + (onBenchmark ? " active" : "")} onClick={() => setOnBenchmark(true)}>
          <span className="n">✓</span>Benchmark
        </button>
        <div className="foot">
          {info
            ? `${info.kb.conversion_rules} conversion rules · ${info.kb.functions} functions · ${info.mappings} feature mappings`
            : "knowledge base loading…"}
        </div>
      </aside>
      <main>
        <div className="step-head">
          <h1>{onBenchmark ? "Conversion-accuracy benchmark" : STEP_META[stepKey].title}</h1>
          <span className="crumb">
            {platform.label} → Power BI
            {!onBenchmark && ` · step ${stepIdx + 1} of ${steps.length}`}
          </span>
          <div className="wiz">
            <button className="btn ghost" disabled={onBenchmark || stepIdx === 0} onClick={() => goto(stepIdx - 1)}>
              ← Back
            </button>
            <button
              className="btn"
              disabled={onBenchmark || stepIdx === steps.length - 1}
              onClick={() => goto(stepIdx + 1)}
            >
              Next →
            </button>
          </div>
        </div>
        <div className="panel" key={stepKey}>
          <Panel
            platform={platform}
            uploads={uploads}
            setUploads={setUploads}
            discovery={discovery}
            setDiscovery={setDiscovery}
            conversion={conversion}
            setConversion={setConversion}
          />
        </div>
      </main>
    </section>
  );
}
