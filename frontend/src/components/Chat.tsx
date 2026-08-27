import { useState } from "react";
import { useAgentStore } from "../store/agentStore";
import ToolCallCard from "./ToolCallCard";
import ApprovalModal from "./ApprovalModal";

export default function Chat() {
  const { messages, toolCalls, pendingApprovals, sendMessage, respondApproval } = useAgentStore();
  const [input, setInput] = useState("");

  const onSend = () => {
    if (!input.trim()) return;
    sendMessage(input.trim());
    setInput("");
  };

  return (
    <div className="flex h-full flex-col bg-black">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && <p className="text-zinc-500 text-sm">输入一句话任务，体验 M1 核心闭环（审批流占位已打通）</p>}
        {messages.map((m) => (
          <div key={m.id} className={`rounded-lg px-3 py-2 text-sm ${m.role === "user" ? "bg-zinc-900 border border-zinc-800 ml-12" : "bg-zinc-950 border border-zinc-800 mr-12"} ${m.streaming ? "animate-pulse" : ""}`}>
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

      <div className="border-t border-zinc-800 p-3 flex gap-2 bg-zinc-950">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSend()}
          placeholder="输入任务..."
          className="flex-1 rounded bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <button onClick={onSend} className="rounded bg-cyan-500 px-4 py-2 text-sm font-medium text-black hover:bg-cyan-400">
          发送
        </button>
      </div>
    </div>
  );
}
