/** M5: 文件 diff 展示 — 依赖 M3 落库的 ToolLog */

export default function CodeDiff({ oldText, newText }: { oldText: string; newText: string }) {
  return (
    <div className="grid grid-cols-2 gap-2 text-xs font-mono">
      <pre className="bg-red-950/20 p-2 rounded border border-red-900/30">{oldText}</pre>
      <pre className="bg-green-950/20 p-2 rounded border border-green-900/30">{newText}</pre>
    </div>
  );
}
