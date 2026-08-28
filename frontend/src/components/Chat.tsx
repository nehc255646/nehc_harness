import { useState } from "react";
import { useAgentStore } from "../store/agentStore";
import ApprovalModal from "./ApprovalModal";
import ToolCallCard from "./ToolCallCard";

export default function Chat() {
  const { messages, toolCalls, pendingApprovals, sendMessage, sendBlockedReason, models, modelId, respondApproval } = useAgentStore();
  const [input, setInput] = useState("");
  const blocked = models.length > 0 && !modelId;

  const onSend = () => {
    if (!input.trim()) return;
    sendMessage(input.trim());
    setInput("");
  };

  return (
    <div className="flex h-full flex-col bg-black">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-zinc-500 text-sm">
            {blocked ? "请先在顶栏选择模型，或在「模型」中创建供应商。" : "输入一句话任务。重启后端后历史仍在，可续聊。"}
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`rounded-lg px-3 py-2 text-sm ${m.role === "user" ? "bg-zinc-900 border border-zinc-800 ml-12" : "bg-zinc-950 border border-zinc-800 mr-12"} ${m.streaming ? "animate-pulse" : ""}`}
          >
            <span className="text-xs text-zinc-500">{m.role}</span>
            <p className="whitespace-pre-wrap">{m.content}</p>
          </div>
        ))}
        {toolCalls.map((t) => (
          <ToolCallCard key={t.call_id} tool={t} />
        ))}
      </div>

      {pendingApprovals.map((a) => (
        <ApprovalModal key={a.approval_id} approval={a} onRespond={respondApproval} />
      ))}

      {sendBlockedReason && <p className="px-3 text-xs text-yellow-400">{sendBlockedReason}</p>}

      <div className="border-t border-zinc-800 p-3 flex gap-2 bg-zinc-950">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSend()}
          placeholder={blocked ? "请先选择模型..." : "输入任务..."}
          disabled={blocked}
          className="flex-1 rounded bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm outline-none focus:border-cyan-500 disabled:opacity-50"
        />
        <button
          onClick={onSend}
          disabled={blocked}
          className="rounded bg-cyan-500 px-4 py-2 text-sm font-medium text-black hover:bg-cyan-400 disabled:opacity-50"
        >
          发送
        </button>
      </div>
    </div>
  );
}
