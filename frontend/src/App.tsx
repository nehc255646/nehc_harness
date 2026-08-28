import { useEffect, useState } from "react";
import { useAgentStore } from "./store/agentStore";
import Chat from "./components/Chat";
import ModelSettings from "./components/ModelSettings";
import SessionSidebar from "./components/SessionSidebar";
import SubAgentPanel from "./components/subagent/SubAgentPanel";
import WorkerStatus from "./components/subagent/WorkerStatus";

export default function App() {
  const { boot, connectionState, models, modelId, setSessionModel, refreshModels } = useAgentStore();
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    boot();
  }, [boot]);

  return (
    <div className="flex h-screen bg-black text-white">
      <SessionSidebar />
      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950 px-4 py-3">
          <h1 className="text-sm font-semibold tracking-wide">
            Agent <span className="text-cyan-400">Harness</span>
          </h1>
          <div className="flex items-center gap-3">
            <select
              className="max-w-56 rounded bg-zinc-900 border border-zinc-800 px-2 py-1 text-xs outline-none focus:border-cyan-500"
              value={modelId ?? ""}
              onChange={(e) => setSessionModel(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{models.length ? "选择模型…" : "演示模式（无模型）"}</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name} ({m.provider_name})
                </option>
              ))}
            </select>
            <button onClick={() => setSettingsOpen(true)} className="text-xs text-cyan-400 hover:underline">
              模型
            </button>
            <span
              className={`h-2 w-2 rounded-full ${connectionState === "connected" ? "bg-cyan-400 shadow shadow-cyan-400/50" : connectionState === "connecting" ? "bg-yellow-400" : "bg-red-500"}`}
              title={connectionState}
            />
          </div>
        </header>
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 overflow-hidden">
            <Chat />
          </div>
          <SubAgentPanel />
        </div>
        <WorkerStatus />
      </div>
      <ModelSettings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onChanged={() => {
          refreshModels();
        }}
      />
    </div>
  );
}
