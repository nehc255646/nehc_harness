/** 交互型子 agent 侧栏 — 用户从顶栏呼出；关闭仅收起 UI，不终止 */
import { useEffect, useRef, useState } from "react";
import { useAgentStore } from "../../store/agentStore";
import ThinkingBlock from "../ThinkingBlock";
import { IconChevron, IconClose, IconSend } from "../icons";

export default function SubAgentPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { subPanels, dismissedPanels, sendSubagentMessage, dismissPanel } = useAgentStore();
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const visible = subPanels.filter((p) => !dismissedPanels.includes(p.subagent_id));
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [subPanels]);

  if (!open) return null;

  const hideCard = (id: string) => {
    dismissPanel(id);
    if (visible.length <= 1) onClose();
  };

  return (
    <>
      <button
        type="button"
        aria-label="关闭子 agent 面板"
        className="fixed inset-0 z-30 bg-black/50 lg:hidden"
        onClick={onClose}
      />
    <aside className="fixed inset-y-0 right-0 z-40 flex w-[min(20rem,92vw)] flex-col gap-3 overflow-y-auto border-l border-[var(--color-border)] bg-surface p-3 shadow-xl lg:static lg:z-auto lg:w-80 lg:shrink-0 lg:shadow-none">
      <div className="flex items-center justify-between gap-2 px-1">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">
          交互子 Agent{visible.length ? ` · ${visible.length}` : ""}
        </h3>
        <button
          type="button"
          onClick={onClose}
          title="收起面板（不终止子 agent）"
          aria-label="收起子 agent 面板"
          className="rounded-md p-1 text-faint hover:bg-surface-2 hover:text-white"
        >
          <IconChevron className="h-4 w-4" />
        </button>
      </div>
      {visible.length === 0 && (
        <p className="px-1 text-xs text-faint">正在打开侧栏对话…</p>
      )}
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
                onClick={() => hideCard(p.subagent_id)}
                title="收起此卡片（不终止子 agent）"
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
                  {m.role !== "user" && (
                    <ThinkingBlock text={m.thinking} streaming={m.thinkingStreaming} />
                  )}
                  <span className="whitespace-pre-wrap">{m.content}</span>
                  {m.streaming && !m.thinkingStreaming && (
                    <span className="ml-0.5 animate-pulse text-accent">▍</span>
                  )}
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
    </>
  );
}
