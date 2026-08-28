import { useState } from "react";
import { wsClient } from "../api/ws";
import { useAgentStore } from "../store/agentStore";
import { IconBolt, IconPlus, IconTrash } from "./icons";

function shortTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function SessionSidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean;
  onClose: () => void;
}) {
  const { sessionId, sessionRows, deleteSession, renameSession, agentState } = useAgentStore();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const commit = async (id: string) => {
    const title = draft.trim();
    setEditing(null);
    if (title) await renameSession(id, title);
  };

  return (
    <>
      {mobileOpen && (
        <button
          aria-label="关闭侧栏"
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={onClose}
        />
      )}
    <aside
      className={`w-64 shrink-0 flex-col border-r border-[var(--color-border)] bg-surface ${
        mobileOpen ? "fixed inset-y-0 left-0 z-40 flex md:static" : "hidden md:flex"
      }`}
    >
      <div className="flex items-center gap-2.5 px-4 py-4">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent-dim text-accent">
          <IconBolt className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold tracking-tight">Harness</div>
          <div className="text-[11px] text-faint">coding agent</div>
        </div>
      </div>

      <div className="px-3 pb-3">
        <button
          onClick={() => wsClient.send("session.create", { title: "New Session" })}
          className="ui-btn-primary w-full"
        >
          <IconPlus className="h-4 w-4" />
          新建会话
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {sessionRows.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-faint">还没有会话</p>
        )}
        {sessionRows.map((s) => {
          const active = s.id === sessionId;
          return (
            <div
              key={s.id}
              className={`group flex items-center rounded-lg px-2 py-1.5 ${
                active ? "bg-accent-dim text-white" : "text-muted hover:bg-surface-2 hover:text-[var(--color-text)]"
              }`}
            >
              {editing === s.id ? (
                <input
                  autoFocus
                  className="ui-input min-w-0 flex-1 px-1.5 py-0.5 text-xs"
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
                  className="min-w-0 flex-1 text-left"
                  onClick={() => {
                    if (s.id !== sessionId) wsClient.send("session.select", { session_id: s.id });
                    onClose();
                  }}
                  onDoubleClick={() => {
                    setEditing(s.id);
                    setDraft(s.title || "");
                  }}
                  title={`${s.title || s.id.slice(0, 8)}\n双击重命名 · ${shortTime(s.updated_at)}`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[13px] font-medium">{s.title || s.id.slice(0, 8)}</span>
                    {active && agentState === "running" && (
                      <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-400" />
                    )}
                  </div>
                  <div className="truncate text-[10px] text-faint">{shortTime(s.updated_at)}</div>
                </button>
              )}
              <button
                className="ml-1 hidden rounded-md p-1 text-faint hover:bg-red-950/50 hover:text-red-300 group-hover:block"
                onClick={() => deleteSession(s.id)}
                title="删除"
              >
                <IconTrash />
              </button>
            </div>
          );
        })}
      </div>
      <p className="border-t border-[var(--color-border)] px-4 py-2 text-[10px] text-faint">双击标题可重命名</p>
    </aside>
    </>
  );
}
