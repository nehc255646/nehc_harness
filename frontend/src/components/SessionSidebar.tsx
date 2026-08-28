import { wsClient } from "../api/ws";
import { useAgentStore } from "../store/agentStore";

export default function SessionSidebar() {
  const { sessionId, sessionRows, deleteSession } = useAgentStore();

  return (
    <aside className="w-60 border-r border-zinc-800 bg-zinc-950 p-3 flex flex-col gap-2">
      <button
        onClick={() => wsClient.send("session.create", { title: "New Session" })}
        className="rounded bg-cyan-500 py-2 text-sm font-medium text-black hover:bg-cyan-400"
      >
        + 新建会话
      </button>
      <div className="mt-2 space-y-1 overflow-y-auto">
        {sessionRows.map((s) => (
          <div
            key={s.id}
            className={`group flex items-center rounded px-2 py-1 text-sm ${s.id === sessionId ? "bg-zinc-900 text-cyan-400" : "text-zinc-400 hover:bg-zinc-900"}`}
          >
            <button
              className="min-w-0 flex-1 truncate text-left"
              onClick={() => {
                if (s.id !== sessionId) wsClient.send("session.select", { session_id: s.id });
              }}
              title={s.title}
            >
              {s.title || s.id.slice(0, 8)}
            </button>
            <button
              className="ml-1 hidden text-xs text-zinc-600 hover:text-red-400 group-hover:block"
              onClick={() => deleteSession(s.id)}
              title="删除"
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <p className="mt-auto text-xs text-zinc-600">会话持久化于 MySQL</p>
    </aside>
  );
}
