import { useEffect } from "react";
import { useAgentStore } from "./store/agentStore";
import Chat from "./components/Chat";
import SessionSidebar from "./components/SessionSidebar";
import SubAgentPanel from "./components/subagent/SubAgentPanel";
import WorkerStatus from "./components/subagent/WorkerStatus";

export default function App() {
  const { connect, connectionState } = useAgentStore();

  useEffect(() => {
    connect();
  }, [connect]);

  return (
    <div className="flex h-screen bg-black text-white">
      <SessionSidebar />
      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950 px-4 py-3">
          <h1 className="text-sm font-semibold tracking-wide">
            Agent <span className="text-cyan-400">Harness</span>
          </h1>
          <span className={`h-2 w-2 rounded-full ${connectionState === "connected" ? "bg-cyan-400 shadow shadow-cyan-400/50" : connectionState === "connecting" ? "bg-yellow-400" : "bg-red-500"}`} title={connectionState} />
        </header>
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 overflow-hidden">
            <Chat />
          </div>
          <SubAgentPanel />
        </div>
        <WorkerStatus />
      </div>
    </div>
  );
}
