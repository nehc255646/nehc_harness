import { useState } from "react";
import { useAgentStore } from "../store/agentStore";
import ApprovalModal from "./ApprovalModal";
import ToolCallCard from "./ToolCallCard";

export default function Chat() {
  const { messages, toolCalls, pendingApprovals, sendMessage, sendBlockedReason, models, modelId, respondApproval, agentState } =
    useAgentStore();
  const [input, setInput] = useState("");
  const blocked = models.length > 0 && !modelId;

  const onSend = () => {
    if (!input.trim()) return;
    sendMessage(input.trim());
    setInput("");
  };

  return (
    <div className="flex h-full flex-col bg-bg">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="text-sm text-zinc-500">
            {blocked
              ? "请先在顶栏选择模型，或在「模型」中创建供应商。"
              : "输入一句话任务。试试「执行 echo hello」走审批，或「写入 hello.txt」。刷新不断审批。"}
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`rounded-lg px-3 py-2 text-sm ${m.role === "user" ? "ml-12 border border-zinc-800 bg-zinc-900" : "mr-12 border border-zinc-800 bg-zinc-950"}`}
          >
            <span className="text-xs text-zinc-500">{m.role}</span>
            <p className="whitespace-pre-wrap">
              {m.content}
              {m.streaming && <span className="ml-0.5 animate-pulse text-accent">▍</span>}
            </p>
          </div>
        ))}
        {toolCalls.map((t) => (
          <ToolCallCard key={t.call_id} tool={t} />
        ))}
        {agentState === "awaiting_approval" && pendingApprovals.length === 0 && (
          <p className="text-xs text-yellow-500">等待审批…</p>
        )}
      </div>

      {pendingApprovals.map((a) => (
        <ApprovalModal key={a.approval_id} approval={a} onRespond={respondApproval} />
      ))}

      {sendBlockedReason && <p className="px-3 text-xs text-yellow-400">{sendBlockedReason}</p>}

      <div className="flex gap-2 border-t border-zinc-800 bg-zinc-950 p-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSend()}
          placeholder={blocked ? "请先选择模型..." : "输入任务..."}
          disabled={blocked}
          className="flex-1 rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-50"
        />
        <button
          onClick={onSend}
          disabled={blocked}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50"
        >
          发送
        </button>
      </div>
    </div>
  );
}
