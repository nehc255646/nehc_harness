import { IconShield } from "./icons";

type Approval = { approval_id: string; tool: string; args: unknown; reason: string };

function similarPattern(tool: string, args: unknown): string | null {
  if (tool === "shell") {
    const rec = args && typeof args === "object" ? (args as Record<string, unknown>) : {};
    const cmd = String(rec.command ?? rec.cmd ?? "").trim();
    const tokens = cmd.split(/\s+/).filter(Boolean);
    const prefix = tokens.slice(0, 2).join(" ");
    return prefix || null;
  }
  return tool || null;
}

export default function ApprovalModal({
  approval,
  onRespond,
}: {
  approval: Approval;
  onRespond: (id: string, d: "approve" | "approve_similar" | "reject") => void;
}) {
  const similar = similarPattern(approval.tool, approval.args);
  const detail =
    approval.reason ||
    (() => {
      try {
        return JSON.stringify(approval.args, null, 2);
      } catch {
        return String(approval.args);
      }
    })();

  return (
    <div className="mb-3 rounded-2xl border border-amber-500/30 bg-amber-950/25 p-4 shadow-panel">
      <div className="flex items-start gap-3">
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-amber-500/15 text-amber-300">
          <IconShield />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-amber-100">
            需要审批
            <span className="ml-2 font-mono text-xs text-accent">{approval.tool}</span>
          </p>
          <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 px-3 py-2 font-mono text-[11px] leading-5 text-zinc-300">
            {detail}
          </pre>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => onRespond(approval.approval_id, "approve")}
              className="ui-btn-primary text-xs"
            >
              执行一次
            </button>
            <button
              onClick={() => onRespond(approval.approval_id, "approve_similar")}
              className="ui-btn-ghost text-xs text-accent hover:text-white"
              title={similar ? `之后以「${similar}」开头的命令本会话都会放行` : undefined}
            >
              本次会话同类均执行{similar ? `（${similar}）` : ""}
            </button>
            <button
              onClick={() => onRespond(approval.approval_id, "reject")}
              className="ui-btn-ghost text-xs hover:border-red-500/40 hover:text-red-300"
            >
              拒绝
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
