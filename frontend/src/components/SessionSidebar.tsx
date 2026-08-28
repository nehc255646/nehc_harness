import { useState } from "react";
import { wsClient } from "../api/ws";
import { useAgentStore } from "../store/agentStore";

function shortTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function SessionSidebar() {
  const { sessionId, sessionRows, deleteSession, renameSession, agentState } = useAgentStore();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const commit = async (id: string) => {
    const title = draft.trim();
    setEditing(null);
    if (title) await renameSession(id, title);
  };

  return (
    <aside className="flex w-60 flex-col gap-2 border-r border-zinc-800 bg-zinc-950 p-3">
      <button
        onClick={() => wsClient.send("session.create", { title: "New Session" })}
        className="rounded bg-accent py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover"
      >
        + 新建会话
      </button>
      <div className="mt-2 space-y-1 overflow-y-auto">
        {sessionRows.map((s) => (
          <div
            key={s.id}
            className={`group flex items-center rounded px-2 py-1 text-sm ${s.id === sessionId ? "bg-zinc-900 text-accent" : "text-zinc-400 hover:bg-zinc-900"}`}
          >
            {editing === s.id ? (
              <input
                autoFocus
                className="min-w-0 flex-1 rounded border border-accent bg-zinc-950 px-1 py-0.5 text-xs text-white outline-none"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => commit(s.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void commit(s.id);
                  if (e.key === "Escape") setEditing(null);
                }}
              />
            ) : (
              <button
                className="min-w-0 flex-1 truncate text-left"
                onClick={() => {
                  if (s.id !== sessionId) wsClient.send("session.select", { session_id: s.id });
                }}
                onDoubleClick={() => {
                  setEditing(s.id);
                  setDraft(s.title || "");
                }}
                title={`${s.title || s.id.slice(0, 8)}\n双击重命名 · ${shortTime(s.updated_at)}`}
              >
                {s.title || s.id.slice(0, 8)}
                {s.id === sessionId && agentState === "running" && <span className="ml-1 text-[10px] text-yellow-400">●</span>}
              </button>
            )}
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
      <p className="mt-auto text-xs text-zinc-600">双击重命名 · 持久化于 MySQL</p>
    </aside>
  );
}
