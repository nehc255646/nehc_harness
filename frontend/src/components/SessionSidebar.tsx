import { useAgentStore } from "../store/agentStore";
import { wsClient } from "../api/ws";

export default function SessionSidebar() {
  const { sessionId, sessions } = useAgentStore();

  return (
    <aside className="w-60 border-r border-zinc-800 bg-zinc-950 p-3 flex flex-col gap-2">
      <button
        onClick={() => wsClient.send("session.create", { title: "New Session" })}
        className="rounded bg-cyan-500 py-2 text-sm font-medium text-black hover:bg-cyan-400"
      >
        + 新建会话
      </button>
      <div className="mt-2 space-y-1">
        {sessions.map((s) => (
          <button
            key={s}
            onClick={() => {
              if (s !== sessionId) wsClient.send("session.select", { session_id: s });
            }}
            className={`w-full rounded px-2 py-1 text-left text-sm truncate ${s === sessionId ? "bg-zinc-900 text-cyan-400" : "text-zinc-400 hover:bg-zinc-900"}`}
          >
            {s}
          </button>
        ))}
      </div>
      <p className="mt-auto text-xs text-zinc-600">M5 多会话侧栏占位</p>
    </aside>
  );
}
