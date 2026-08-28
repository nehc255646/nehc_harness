/** 交互型子 agent 侧栏面板 — M2 实现 */
import { useState } from "react";
import { useAgentStore } from "../../store/agentStore";

export default function SubAgentPanel() {
  const { subPanels, sendSubagentMessage } = useAgentStore();
  const [inputs, setInputs] = useState<Record<string, string>>({});

  if (subPanels.length === 0) {
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
      <h3 className="text-xs font-semibold text-zinc-400">子 Agent 面板 ({subPanels.length})</h3>
      {subPanels.map((p) => (
        <div key={p.subagent_id} className="rounded border border-zinc-800 bg-zinc-900 p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-cyan-400">{p.subagent_id}</span>
            <span className={`text-xs ${p.status === "running" ? "text-yellow-400" : p.status === "done" ? "text-green-400" : "text-red-400"}`}>{p.status}</span>
          </div>
          <p className="mt-1 text-xs text-zinc-300 line-clamp-2">{p.task}</p>
          {p.result && <p className="mt-1 text-xs text-zinc-500">结果: {p.result.slice(0, 200)}</p>}
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
                className="flex-1 rounded bg-zinc-950 border border-zinc-800 px-2 py-1 text-xs outline-none focus:border-cyan-500"
              />
              <button
                onClick={() => {
                  if (!inputs[p.subagent_id]?.trim()) return;
                  sendSubagentMessage(p.subagent_id, inputs[p.subagent_id].trim());
                  setInputs((s) => ({ ...s, [p.subagent_id]: "" }));
                }}
                className="rounded bg-cyan-500 px-2 py-1 text-xs text-black"
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
