/** 右侧栏：顶部工作型摘要 + 下方可对话的交互子 agent */
import { useEffect, useRef, useState } from "react";
import { useAgentStore } from "../../store/agentStore";
import ThinkingBlock from "../ThinkingBlock";
import { IconChevron, IconSend } from "../icons";
import WorkerStatus from "./WorkerStatus";

export default function SubAgentPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { subPanels, dismissedPanels, sendSubagentMessage, startInteractive } = useAgentStore();
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const visible = subPanels.filter((p) => !dismissedPanels.includes(p.subagent_id));
  const active = visible.find((p) => p.status === "running") || visible[visible.length - 1];
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [active?.messages, active?.subagent_id]);

  if (!open) return null;

  const send = () => {
    if (!active || active.status !== "running") return;
    const text = (inputs[active.subagent_id] || "").trim();
    if (!text) return;
    sendSubagentMessage(active.subagent_id, text);
    setInputs((s) => ({ ...s, [active.subagent_id]: "" }));
  };

  return (
    <>
      <button
        type="button"
        aria-label="关闭子 agent 侧栏"
        className="fixed inset-0 z-30 bg-black/50 lg:hidden"
        onClick={onClose}
      />
      <aside className="fixed inset-y-0 right-0 z-40 flex w-[min(22rem,92vw)] flex-col border-l border-[var(--color-border)] bg-surface shadow-xl lg:static lg:z-auto lg:w-80 lg:shrink-0 lg:shadow-none">
        <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-[var(--color-border)] px-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">子 Agent</h3>
          <button
            type="button"
            onClick={onClose}
            title="收起侧栏（不终止子 agent）"
            aria-label="收起子 agent 侧栏"
            className="rounded-md p-1 text-faint hover:bg-surface-2 hover:text-white"
          >
            <IconChevron className="h-4 w-4" />
          </button>
        </div>

        <WorkerStatus />

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-faint">交互对话</span>
            {active && (
              <span
                className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                  active.status === "running"
                    ? "bg-amber-500/15 text-amber-300"
                    : active.status === "done"
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-red-500/15 text-red-300"
                }`}
              >
                {active.status === "running" ? "对话中" : active.status === "done" ? "已结束" : active.status}
              </span>
            )}
          </div>

          {!active && (
            <div className="flex flex-1 flex-col items-start justify-center gap-3 px-4 pb-8">
              <p className="text-sm text-zinc-200">还没有侧栏对话</p>
              <p className="text-xs leading-relaxed text-faint">
                这是你可以单独聊天的交互子 Agent，不影响主对话。结束后会把摘要回投给主 agent。
              </p>
              <button type="button" className="ui-btn-primary text-xs" onClick={() => startInteractive()}>
                开始侧栏对话
              </button>
            </div>
          )}

          {active && (
            <>
              <p className="line-clamp-2 px-3 text-[11px] text-muted">{active.task}</p>
              <div className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto px-3">
                {(active.messages || []).map((m) => (
                  <div
                    key={m.id}
                    className={`rounded-lg px-2 py-1.5 text-xs ${
                      m.role === "user" ? "bg-surface-2 text-zinc-200" : "bg-accent-dim text-accent"
                    }`}
                  >
                    <span className="mr-1 text-[10px] text-faint">{m.role === "user" ? "我" : "子"}</span>
                    {m.role !== "user" && <ThinkingBlock text={m.thinking} streaming={m.thinkingStreaming} />}
                    <span className="whitespace-pre-wrap">{m.content}</span>
                    {m.streaming && !m.thinkingStreaming && (
                      <span className="ml-0.5 animate-pulse text-accent">▍</span>
                    )}
                  </div>
                ))}
                {active.result && active.status !== "running" && (
                  <p className="rounded-lg bg-black/30 px-2 py-1.5 text-[11px] text-faint">回投：{active.result}</p>
                )}
                <div ref={bottomRef} />
              </div>
              {active.status === "running" ? (
                <div className="shrink-0 border-t border-[var(--color-border)] p-2">
                  <div className="flex gap-1.5">
                    <input
                      value={inputs[active.subagent_id] || ""}
                      onChange={(e) => setInputs((s) => ({ ...s, [active.subagent_id]: e.target.value }))}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") send();
                      }}
                      placeholder="对交互子 agent 说话…"
                      className="ui-input flex-1 px-2 py-1.5 text-xs"
                    />
                    <button type="button" onClick={send} className="ui-btn-primary px-2 py-1.5">
                      <IconSend className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ) : (
                <div className="shrink-0 border-t border-[var(--color-border)] px-3 py-2">
                  <button type="button" className="ui-btn-ghost w-full text-xs" onClick={() => startInteractive()}>
                    新开侧栏对话
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
