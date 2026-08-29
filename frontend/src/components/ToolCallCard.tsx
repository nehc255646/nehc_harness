import { useState } from "react";
import CodeDiff, { type FileDiff } from "./CodeDiff";
import { IconChevron, IconFile, IconTerminal } from "./icons";

export type ToolCallView = {
  call_id: string;
  name: string;
  args: unknown;
  result?: unknown;
  diff?: FileDiff;
  progress?: string;
};

function resultText(result: unknown): string {
  if (result == null) return "";
  if (typeof result === "string") return result;
  if (typeof result === "object" && result !== null && "text" in result) {
    return String((result as { text?: unknown }).text ?? "");
  }
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

function summarize(name: string, args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  if (typeof a.command === "string") return a.command;
  if (typeof a.path === "string") return a.path;
  if (typeof a.pattern === "string") return a.pattern;
  if (typeof a.query === "string") return a.query;
  try {
    const s = JSON.stringify(args);
    return s.length > 80 ? `${s.slice(0, 80)}…` : s;
  } catch {
    return "";
  }
}

export default function ToolCallCard({ tool }: { tool: ToolCallView }) {
  const running = tool.result === undefined;
  const text = resultText(tool.result);
  const isErr = !running && /\[错误\]|\[拒绝\]|\[超时\]|\[异常\]/.test(text);
  const [open, setOpen] = useState(() => running || isErr);
  const diff = tool.diff;
  const summary = summarize(tool.name, tool.args);
  const fileLike = tool.name === "write" || tool.name === "edit" || tool.name === "read";

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.02]"
      >
        <span className={`text-faint transition ${open ? "rotate-90" : ""}`}>
          <IconChevron />
        </span>
        <span className="text-accent">{fileLike ? <IconFile /> : <IconTerminal />}</span>
        <span className="font-mono text-xs font-medium text-accent">{tool.name}</span>
        {summary && <span className="min-w-0 truncate font-mono text-[11px] text-muted">{summary}</span>}
        <span className="ml-auto shrink-0 font-mono text-[10px] text-faint">{tool.call_id.slice(0, 8)}</span>
        {running && (
          <span className="shrink-0 rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">进行中</span>
        )}
        {isErr && (
          <span className="shrink-0 rounded-full bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-300">失败</span>
        )}
      </button>
      {open && (
        <div className="border-t border-[var(--color-border)] px-3 py-2">
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-5 text-muted">{JSON.stringify(tool.args, null, 2)}</pre>
          {running && tool.progress && (
            <pre className="mt-2 max-h-32 overflow-y-auto border-t border-[var(--color-border)] pt-2 font-mono text-[11px] text-faint">
              {tool.progress}
              <span className="ml-0.5 animate-pulse text-accent">▍</span>
            </pre>
          )}
          {!running && text && (
            <pre className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap border-t border-[var(--color-border)] pt-2 font-mono text-[11px] leading-5 text-zinc-300">
              {text}
            </pre>
          )}
          {diff && <CodeDiff diff={diff} />}
        </div>
      )}
    </div>
  );
}
