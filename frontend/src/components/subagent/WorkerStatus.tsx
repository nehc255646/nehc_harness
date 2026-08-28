/** 后台工作型列表 — M2 实现 */
import { useAgentStore } from "../../store/agentStore";

export default function WorkerStatus() {
  const { workers, stopWorker } = useAgentStore();

  if (workers.length === 0) {
    return (
      <div className="border-t border-zinc-800 bg-zinc-950 px-3 py-2">
        <span className="text-xs text-zinc-600">工作区 — 暂无后台任务（spawn_worker 后台并发，M2）</span>
      </div>
    );
  }

  return (
    <div className="border-t border-zinc-800 bg-zinc-950 px-3 py-2">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-semibold text-zinc-400">工作区</span>
        <span className="h-2 w-2 rounded-full bg-accent shadow-[0_0_8px_var(--color-accent-glow)]" />
        <span className="text-xs text-zinc-500">{workers.length} 个后台任务</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {workers.map((w) => (
          <div key={w.subagent_id} className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1 flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${w.state === "running" ? "bg-yellow-400 animate-pulse" : w.state === "done" ? "bg-green-400" : "bg-red-400"}`} />
            <span className="text-xs font-mono text-zinc-300">{w.subagent_id}</span>
            <span className="text-xs text-zinc-500 truncate max-w-40">{w.task_summary}</span>
            <span className="text-xs text-zinc-600">{w.state}</span>
            {w.state === "running" && (
              <button
                onClick={() => stopWorker(w.subagent_id)}
                title="终止该工作型子 agent"
                className="rounded px-1 text-xs text-zinc-500 hover:bg-red-900/40 hover:text-red-300"
              >
                ✕
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
