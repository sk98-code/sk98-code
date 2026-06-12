import { useEffect, useState } from "react";
import { api } from "./api.js";
import { PLATFORMS } from "./platforms.js";
import PlatformSelect from "./components/PlatformSelect.jsx";
import Workspace from "./components/Workspace.jsx";

export default function App() {
  const [platformKey, setPlatformKey] = useState(null);
  const [info, setInfo] = useState(null);

  useEffect(() => {
    api("/api/info").then(setInfo).catch(() => setInfo(null));
  }, []);

  useEffect(() => {
    const color = platformKey ? PLATFORMS[platformKey].color : "#4da3ff";
    document.documentElement.style.setProperty("--pc", color);
  }, [platformKey]);

  const leaveWorkspace = async () => {
    try {
      await api("/api/reset", { method: "POST" });
    } catch {
      /* workspace reset is best-effort when navigating away */
    }
    setPlatformKey(null);
  };

  if (!platformKey) return <PlatformSelect onSelect={setPlatformKey} />;
  return <Workspace platform={PLATFORMS[platformKey]} info={info} onLeave={leaveWorkspace} />;
}
