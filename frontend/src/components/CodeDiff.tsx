/** 文件 diff 展示 — 按行 LCS，依赖 write/edit ToolLog 的 old_text/new_text */

export type FileDiff = { path?: string; old_text: string; new_text: string };

type Row = { type: "eq" | "del" | "add"; text: string };

const MAX_LINES = 800;

function lineDiff(oldText: string, newText: string): Row[] {
  const a = (oldText ?? "").split("\n").slice(0, MAX_LINES);
  const b = (newText ?? "").split("\n").slice(0, MAX_LINES);
  const n = a.length;
  const m = b.length;
  if (n * m > 250_000) {
    return [...a.map((text) => ({ type: "del" as const, text })), ...b.map((text) => ({ type: "add" as const, text }))];
  }
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows: Row[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      rows.push({ type: "eq", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: "del", text: a[i] });
      i++;
    } else {
      rows.push({ type: "add", text: b[j] });
      j++;
    }
  }
  while (i < n) {
    rows.push({ type: "del", text: a[i++] });
  }
  while (j < m) {
    rows.push({ type: "add", text: b[j++] });
  }
  return rows;
}

export default function CodeDiff({ diff }: { diff: FileDiff }) {
  const rows = lineDiff(diff.old_text, diff.new_text);
  if (rows.length === 0 || (rows.length === 1 && rows[0].text === "" && diff.old_text === diff.new_text)) {
    return null;
  }
  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-[var(--color-border)] bg-black/30">
      {diff.path && (
        <div className="border-b border-[var(--color-border)] px-3 py-1.5 font-mono text-[11px] text-muted">{diff.path}</div>
      )}
      <pre className="max-h-64 overflow-auto py-1 font-mono text-[11px] leading-5">
        {rows.map((r, idx) => (
          <div
            key={idx}
            className={
              r.type === "del"
                ? "bg-red-500/10 text-red-300"
                : r.type === "add"
                  ? "bg-emerald-500/10 text-emerald-300"
                  : "text-zinc-500"
            }
          >
            <span className="inline-block w-6 select-none text-center text-faint">
              {r.type === "del" ? "−" : r.type === "add" ? "+" : " "}
            </span>
            {r.text || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}
