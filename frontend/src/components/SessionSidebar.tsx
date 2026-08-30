import { useEffect, useState } from "react";
import { useAgentStore } from "../store/agentStore";
import { IconBolt, IconChevron, IconPlus, IconTrash } from "./icons";

function shortTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function SessionSidebar({
  mobileOpen,
  collapsed,
  onClose,
  onToggleCollapsed,
}: {
  mobileOpen: boolean;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapsed: () => void;
}) {
  const { sessionId, sessionRows, deleteSession, renameSession, agentState, selectSession, createSession } =
    useAgentStore();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [createHint, setCreateHint] = useState("");
  const rail = collapsed && !mobileOpen;

  useEffect(() => {
    if (!createHint) return;
    const t = window.setTimeout(() => setCreateHint(""), 2200);
    return () => window.clearTimeout(t);
  }, [createHint]);

  const commit = async (id: string) => {
    const title = draft.trim();
    setEditing(null);
    if (title) await renameSession(id, title);
  };

  const onCreateSession = () => {
    setCreateHint("");
    createSession();
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
      className={`shrink-0 flex-col border-r border-[var(--color-border)] bg-surface transition-[width] duration-200 ${
        mobileOpen ? "fixed inset-y-0 left-0 z-40 flex w-64 md:static md:z-auto" : "hidden md:flex"
      } ${rail ? "md:w-14" : "w-64"}`}
    >
      <div className={`flex items-center py-4 ${rail ? "flex-col gap-2 px-2" : "gap-2.5 px-3"}`}>
        {rail ? (
          <button
            type="button"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent-dim text-accent"
            title="展开会话列表"
            onClick={onToggleCollapsed}
          >
            <IconBolt className="h-4 w-4" />
          </button>
        ) : (
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent-dim text-accent">
            <IconBolt className="h-4 w-4" />
          </div>
        )}
        {!rail && (
          <>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold tracking-tight">Neharness</div>
              <div className="text-[11px] text-faint">coding agent</div>
            </div>
            <button
              type="button"
              className="hidden rounded-md p-1 text-faint hover:bg-surface-2 hover:text-white md:inline-flex"
              title="收起会话列表"
              aria-label="收起会话列表"
              onClick={onToggleCollapsed}
            >
              <IconChevron className="h-4 w-4 rotate-180" />
            </button>
          </>
        )}
      </div>

      <div className={`relative pb-3 ${rail ? "px-2" : "px-3"}`}>
        <button
          type="button"
          onClick={onCreateSession}
          className={rail ? "ui-btn-primary h-9 w-full p-0" : "ui-btn-primary w-full"}
          title="新建会话"
          aria-label="新建会话"
        >
          <IconPlus className="h-4 w-4" />
          {!rail && "新建会话"}
        </button>
        {createHint && (
          <p
            role="status"
            className={
              rail
                ? "absolute left-full top-1 z-50 ml-2 whitespace-nowrap rounded-md border border-[var(--color-border)] bg-surface-2 px-2 py-1 text-[11px] text-muted shadow-lg"
                : "mt-2 text-center text-[11px] text-faint"
            }
          >
            {createHint}
          </p>
        )}
      </div>

      {rail ? (
        <div className="mt-auto px-2 pb-3">
          <button
            type="button"
            className="grid h-9 w-full place-items-center rounded-lg text-faint hover:bg-surface-2 hover:text-white"
            title="展开会话列表"
            aria-label="展开会话列表"
            onClick={onToggleCollapsed}
          >
            <IconChevron className="h-4 w-4" />
          </button>
        </div>
      ) : (
      <>
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
                    if (s.id !== sessionId) selectSession(s.id);
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
                onClick={() => {
                  if (window.confirm(`删除会话「${s.title || s.id.slice(0, 8)}」？`)) void deleteSession(s.id);
                }}
                title="删除"
              >
                <IconTrash />
              </button>
            </div>
          );
        })}
      </div>
      <p className="border-t border-[var(--color-border)] px-4 py-2 text-[10px] text-faint">双击标题可重命名</p>
      </>
      )}
    </aside>
    </>
  );
}
