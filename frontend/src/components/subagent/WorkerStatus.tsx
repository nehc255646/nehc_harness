/** 工作型：侧栏顶部一小块，点击查看详情 */
import { useState } from "react";
import { useAgentStore } from "../../store/agentStore";
import { IconClose } from "../icons";

const STATE_TEXT: Record<string, string> = {
  running: "工作中",
  done: "完成",
  error: "出错",
};

export default function WorkerStatus({ peek = false }: { peek?: boolean }) {
  const { workers, stopWorker, setSubPanelOpen } = useAgentStore();
  const [openId, setOpenId] = useState<string | null>(null);

  if (workers.length === 0) return null;

  const running = workers.filter((w) => w.state === "running").length;
  const selected = workers.find((w) => w.subagent_id === openId) || null;

  const onChip = (id: string) => {
    if (peek) {
      setSubPanelOpen(true);
      setOpenId(id);
      return;
    }
    setOpenId((cur) => (cur === id ? null : id));
  };

  return (
    <div className={`shrink-0 ${peek ? "border-b" : "border-b"} border-[var(--color-border)] bg-surface px-3 py-2`}>
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-faint">工作型</span>
        {running > 0 && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />}
        <span className="text-xs text-muted">
          {running > 0 ? `${running} 个在工作` : `${workers.length} 个已结束`}
        </span>
        {peek && (
          <button type="button" className="ml-auto text-[11px] text-accent" onClick={() => setSubPanelOpen(true)}>
            在侧栏查看
          </button>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {workers.map((w) => (
          <button
            type="button"
            key={w.subagent_id}
            onClick={() => onChip(w.subagent_id)}
            title={w.task_summary || w.subagent_id}
            className={`flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-left text-[11px] ${
              openId === w.subagent_id
                ? "border-accent/40 bg-accent-dim text-accent"
                : "border-[var(--color-border)] bg-surface-2 text-zinc-300 hover:border-[var(--color-border-strong)]"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                w.state === "running" ? "animate-pulse bg-amber-400" : w.state === "done" ? "bg-emerald-400" : "bg-red-400"
              }`}
            />
            <span className="max-w-[9rem] truncate">{w.task_summary || w.subagent_id}</span>
          </button>
        ))}
      </div>
      {!peek && selected && (
        <div className="mt-2 rounded-lg border border-[var(--color-border)] bg-surface-2 p-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-mono text-[10px] text-faint">{selected.subagent_id}</p>
              <p className="mt-0.5 text-xs text-zinc-200">{selected.task_summary}</p>
              <p className="mt-0.5 text-[11px] text-muted">
                {STATE_TEXT[selected.state] || selected.state}
                {selected.late ? " · 迟到" : ""}
              </p>
            </div>
            {selected.state === "running" && (
              <button
                type="button"
                onClick={() => stopWorker(selected.subagent_id)}
                title="终止该工人"
                className="rounded p-0.5 text-faint hover:bg-red-900/40 hover:text-red-300"
              >
                <IconClose className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {selected.result && (
            <p className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap text-[11px] text-faint">{selected.result}</p>
          )}
        </div>
      )}
    </div>
  );
}
