/** 交互型子 agent 侧栏面板 — 对话记录/迟到标记/收起（关闭仅收起 UI，不终止） */
import { useEffect, useRef, useState } from "react";
import { useAgentStore } from "../../store/agentStore";
import { IconClose, IconSend } from "../icons";

export default function SubAgentPanel() {
  const { subPanels, dismissedPanels, sendSubagentMessage, dismissPanel } = useAgentStore();
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const visible = subPanels.filter((p) => !dismissedPanels.includes(p.subagent_id));
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [subPanels]);

  if (visible.length === 0) return null;

  return (
    <aside className="hidden w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l border-[var(--color-border)] bg-surface p-3 lg:flex">
      <h3 className="px-1 text-[11px] font-semibold uppercase tracking-wider text-faint">
        子 Agent · {visible.length}
      </h3>
      {visible.map((p) => (
        <div key={p.subagent_id} className="rounded-xl border border-[var(--color-border)] bg-surface-2 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[11px] text-accent">
              {p.subagent_id}
              {p.late && (
                <span className="ml-1 rounded bg-amber-900/60 px-1 text-[10px] text-amber-300">迟到</span>
              )}
            </span>
            <div className="flex items-center gap-1">
              <span
                className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                  p.status === "running"
                    ? "bg-amber-500/15 text-amber-300"
                    : p.status === "done"
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-red-500/15 text-red-300"
                }`}
              >
                {p.status}
              </span>
              <button
                onClick={() => dismissPanel(p.subagent_id)}
                title="收起面板（不终止子 agent）"
                className="rounded-md p-1 text-faint hover:bg-black/30 hover:text-white"
              >
                <IconClose className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <p className="mt-2 line-clamp-2 text-xs text-muted">{p.task}</p>
          {p.result && <p className="mt-1 text-xs text-faint">结果: {p.result.slice(0, 200)}</p>}
          {(p.messages?.length || 0) > 0 && (
            <div className="mt-2 max-h-48 space-y-1 overflow-y-auto rounded-lg bg-black/30 p-1.5">
              {p.messages.map((m) => (
                <div
                  key={m.id}
                  className={`rounded-lg px-2 py-1 text-xs ${m.role === "user" ? "bg-surface text-zinc-200" : "bg-accent-dim text-accent"}`}
                >
                  <span className="mr-1 text-[10px] text-faint">{m.role === "user" ? "我" : "子"}</span>
                  <span className="whitespace-pre-wrap">{m.content}</span>
                  {m.streaming && <span className="ml-0.5 animate-pulse text-accent">▍</span>}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          )}
          {p.status === "running" ? (
            <div className="mt-2 flex gap-1.5">
              <input
                value={inputs[p.subagent_id] || ""}
                onChange={(e) => setInputs((s) => ({ ...s, [p.subagent_id]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && inputs[p.subagent_id]?.trim()) {
                    sendSubagentMessage(p.subagent_id, inputs[p.subagent_id].trim());
                    setInputs((s) => ({ ...s, [p.subagent_id]: "" }));
                  }
                }}
                placeholder="对子 agent 说话…"
                className="ui-input flex-1 px-2 py-1 text-xs"
              />
              <button
                onClick={() => {
                  if (!inputs[p.subagent_id]?.trim()) return;
                  sendSubagentMessage(p.subagent_id, inputs[p.subagent_id].trim());
                  setInputs((s) => ({ ...s, [p.subagent_id]: "" }));
                }}
                className="ui-btn-primary px-2 py-1"
              >
                <IconSend className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <p className="mt-2 text-[11px] text-faint">已返回结果</p>
          )}
        </div>
      ))}
    </aside>
  );
}
