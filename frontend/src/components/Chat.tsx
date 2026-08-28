import { useEffect, useRef, useState } from "react";
import { useAgentStore } from "../store/agentStore";
import ApprovalModal from "./ApprovalModal";
import { IconSend } from "./icons";
import ToolCallCard from "./ToolCallCard";

const EXAMPLES = ["执行 echo hello", "列出当前工作目录", "写入 hello.txt，内容为 hello harness"];

function roleLabel(role: string) {
  if (role === "user") return "你";
  if (role === "assistant") return "Harness";
  return role;
}

export default function Chat() {
  const { messages, toolCalls, pendingApprovals, sendMessage, sendBlockedReason, models, modelId, respondApproval, agentState } =
    useAgentStore();
  const [input, setInput] = useState("");
  const blocked = models.length > 0 && !modelId;
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const onSend = () => {
    if (!input.trim() || blocked) return;
    sendMessage(input.trim());
    setInput("");
    if (taRef.current) taRef.current.style.height = "auto";
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, toolCalls, pendingApprovals, agentState]);

  const resize = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {messages.length === 0 && toolCalls.length === 0 ? (
          <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6 text-center">
            <div className="mb-3 text-2xl font-semibold tracking-tight">
              下一步做什么？
            </div>
            <p className="max-w-md text-sm text-muted">
              {blocked
                ? "先在顶栏选择模型，或打开「模型」添加供应商。"
                : "给 agent 一条任务。文件写入和命令默认会先请你审批。"}
            </p>
            {!blocked && (
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => {
                      setInput(ex);
                      taRef.current?.focus();
                    }}
                    className="rounded-full border border-[var(--color-border)] bg-surface px-3 py-1.5 text-xs text-muted hover:border-[var(--color-border-strong)] hover:text-white"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
            {messages.map((m) => {
              const isUser = m.role === "user";
              const isError = !isUser && m.content.startsWith("[错误]");
              return (
                <div key={m.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 ${
                      isUser
                        ? "bg-accent-dim text-[var(--color-text)]"
                        : isError
                          ? "border border-red-500/30 bg-red-950/30 text-red-200"
                          : "border border-[var(--color-border)] bg-surface"
                    }`}
                  >
                    <div className={`mb-1 text-[10px] font-medium uppercase tracking-wider ${isUser ? "text-accent" : "text-faint"}`}>
                      {roleLabel(m.role)}
                    </div>
                    <p className="whitespace-pre-wrap">
                      {m.content}
                      {m.streaming && <span className="ml-0.5 animate-pulse text-accent">▍</span>}
                    </p>
                  </div>
                </div>
              );
            })}
            {toolCalls.map((t) => (
              <ToolCallCard key={t.call_id} tool={t} />
            ))}
            {agentState === "awaiting_approval" && pendingApprovals.length === 0 && (
              <p className="text-center text-xs text-amber-400">等待审批…</p>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="border-t border-[var(--color-border)] bg-surface/60 px-4 py-3 backdrop-blur">
        <div className="mx-auto w-full max-w-3xl">
          {pendingApprovals.map((a) => (
            <ApprovalModal key={a.approval_id} approval={a} onRespond={respondApproval} />
          ))}
          {sendBlockedReason && <p className="mb-2 text-xs text-amber-400">{sendBlockedReason}</p>}
          <div className="flex items-end gap-2 rounded-2xl border border-[var(--color-border)] bg-surface-2 p-2 shadow-panel focus-within:border-accent">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                resize();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSend();
                }
              }}
              placeholder={blocked ? "请先选择模型..." : "输入任务，Enter 发送，Shift+Enter 换行"}
              disabled={blocked}
              className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-faint disabled:opacity-50"
            />
            <button onClick={onSend} disabled={blocked || !input.trim()} className="ui-btn-primary h-10 w-10 shrink-0 rounded-xl p-0">
              <IconSend />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
