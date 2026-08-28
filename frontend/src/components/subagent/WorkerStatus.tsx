/** 后台工作型列表 — 仅在有任务时显示 */
import { useAgentStore } from "../../store/agentStore";
import { IconClose } from "../icons";

export default function WorkerStatus() {
  const { workers, stopWorker } = useAgentStore();

  if (workers.length === 0) return null;

  return (
    <div className="shrink-0 border-t border-[var(--color-border)] bg-surface px-4 py-2.5">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-faint">工作区</span>
        <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_8px_var(--color-accent-glow)]" />
        <span className="text-xs text-muted">{workers.length} 个后台任务</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {workers.map((w) => (
          <div
            key={w.subagent_id}
            className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-surface-2 px-2.5 py-1.5"
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                w.state === "running" ? "animate-pulse bg-amber-400" : w.state === "done" ? "bg-emerald-400" : "bg-red-400"
              }`}
            />
            <span className="max-w-40 truncate text-xs text-zinc-300">{w.task_summary || w.subagent_id}</span>
            <span className="text-[10px] text-faint">{w.state}</span>
            {w.state === "running" && (
              <button
                onClick={() => stopWorker(w.subagent_id)}
                title="终止该工作型子 agent"
                className="rounded p-0.5 text-faint hover:bg-red-900/40 hover:text-red-300"
              >
                <IconClose className="h-3 w-3" />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
