export default function ToolCallCard({ tool }: { tool: { call_id: string; name: string; args: unknown; result?: unknown } }) {
  return (
    <div className="rounded border border-cyan-900/50 bg-zinc-950 px-3 py-2 text-xs">
      <div className="font-mono text-cyan-400">{tool.name} <span className="text-zinc-600">{tool.call_id.slice(0, 8)}</span></div>
      <pre className="mt-1 whitespace-pre-wrap text-zinc-400">{JSON.stringify(tool.args, null, 2)}</pre>
      {tool.result !== undefined && <pre className="mt-2 border-t border-zinc-800 pt-2 text-zinc-300">{JSON.stringify(tool.result, null, 2)}</pre>}
    </div>
  );
}
