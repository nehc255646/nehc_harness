import CodeDiff, { type FileDiff } from "./CodeDiff";

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

export default function ToolCallCard({ tool }: { tool: ToolCallView }) {
  const text = resultText(tool.result);
  const diff = tool.diff;
  return (
    <div className="rounded border border-accent/30 bg-zinc-950 px-3 py-2 text-xs">
      <div className="font-mono text-accent">
        {tool.name} <span className="text-zinc-600">{tool.call_id.slice(0, 8)}</span>
      </div>
      <pre className="mt-1 whitespace-pre-wrap text-zinc-400">{JSON.stringify(tool.args, null, 2)}</pre>
      {tool.result === undefined && tool.progress && (
        <pre className="mt-2 max-h-32 overflow-y-auto border-t border-zinc-800 pt-2 font-mono text-[11px] text-zinc-500">
          {tool.progress}
          <span className="ml-0.5 animate-pulse text-accent">▍</span>
        </pre>
      )}
      {tool.result !== undefined && text && (
        <pre className="mt-2 whitespace-pre-wrap border-t border-zinc-800 pt-2 text-zinc-300">{text}</pre>
      )}
      {diff && <CodeDiff diff={diff} />}
    </div>
  );
}
