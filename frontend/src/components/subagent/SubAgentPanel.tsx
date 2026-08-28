/** 交互型子 agent 侧栏面板 — 对话记录/迟到标记/收起（关闭仅收起 UI，不终止） */
import { useEffect, useRef, useState } from "react";
import { useAgentStore } from "../../store/agentStore";

export default function SubAgentPanel() {
  const { subPanels, dismissedPanels, sendSubagentMessage, dismissPanel } = useAgentStore();
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const visible = subPanels.filter((p) => !dismissedPanels.includes(p.subagent_id));
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // 面板有新消息时滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [subPanels]);

  if (visible.length === 0) {
    return (
      <aside className="w-80 border-l border-zinc-800 bg-zinc-950 p-3 hidden lg:block">
        <h3 className="text-xs font-semibold text-zinc-400">子 Agent 面板</h3>
        <p className="mt-2 text-xs text-zinc-600">交互型侧栏（M2）— 暂无活动会话</p>
        <p className="mt-1 text-xs text-zinc-700">主 agent 调用 spawn_subagent 后在此对话，完成将自动回投。</p>
      </aside>
    );
  }

  return (
    <aside className="w-80 border-l border-zinc-800 bg-zinc-950 p-3 flex flex-col gap-3 overflow-y-auto hidden lg:flex">
      <h3 className="text-xs font-semibold text-zinc-400">子 Agent 面板 ({visible.length})</h3>
      {visible.map((p) => (
        <div key={p.subagent_id} className="rounded border border-zinc-800 bg-zinc-900 p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-accent">
              {p.subagent_id}
              {p.late && <span className="ml-1 rounded bg-yellow-900/60 px-1 text-yellow-300">迟到</span>}
            </span>
            <div className="flex items-center gap-1">
              <span className={`text-xs ${p.status === "running" ? "text-yellow-400" : p.status === "done" ? "text-green-400" : "text-red-400"}`}>{p.status}</span>
              <button
                onClick={() => dismissPanel(p.subagent_id)}
                title="收起面板（不终止子 agent）"
                className="rounded px-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
              >
                ✕
              </button>
            </div>
          </div>
          <p className="mt-1 text-xs text-zinc-300 line-clamp-2">{p.task}</p>
          {p.result && <p className="mt-1 text-xs text-zinc-500">结果: {p.result.slice(0, 200)}</p>}
          {(p.messages?.length || 0) > 0 && (
            <div className="mt-2 max-h-48 space-y-1 overflow-y-auto rounded bg-zinc-950 p-1">
              {p.messages.map((m) => (
                <div key={m.id} className={`rounded px-1.5 py-0.5 text-xs ${m.role === "user" ? "bg-zinc-800 text-zinc-200" : "bg-zinc-900 text-accent"}`}>
                  <span className="mr-1 text-[10px] text-zinc-500">{m.role === "user" ? "我" : "子"}</span>
                  <span className="whitespace-pre-wrap">{m.content}</span>
                  {m.streaming && <span className="ml-0.5 animate-pulse text-accent">▍</span>}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          )}
          {p.status === "running" ? (
            <div className="mt-2 flex gap-1">
              <input
                value={inputs[p.subagent_id] || ""}
                onChange={(e) => setInputs((s) => ({ ...s, [p.subagent_id]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && inputs[p.subagent_id]?.trim()) {
                    sendSubagentMessage(p.subagent_id, inputs[p.subagent_id].trim());
                    setInputs((s) => ({ ...s, [p.subagent_id]: "" }));
                  }
                }}
                placeholder="侧栏输入..."
                className="flex-1 rounded bg-zinc-950 border border-zinc-800 px-2 py-1 text-xs outline-none focus:border-accent"
              />
              <button
                onClick={() => {
                  if (!inputs[p.subagent_id]?.trim()) return;
                  sendSubagentMessage(p.subagent_id, inputs[p.subagent_id].trim());
                  setInputs((s) => ({ ...s, [p.subagent_id]: "" }));
                }}
                className="rounded bg-accent px-2 py-1 text-xs text-accent-fg"
              >
                发送
              </button>
            </div>
          ) : (
            <p className="mt-1 text-xs text-zinc-600">已返回结果</p>
          )}
        </div>
      ))}
    </aside>
  );
}
