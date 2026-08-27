type Approval = { approval_id: string; tool: string; args: unknown; reason: string };

export default function ApprovalModal({ approval, onRespond }: { approval: Approval; onRespond: (id: string, d: "approve" | "approve_similar" | "reject") => void }) {
  return (
    <div className="mx-4 mb-2 rounded-lg border border-yellow-600/50 bg-yellow-950/30 p-3">
      <p className="text-sm text-yellow-200">审批请求: <span className="font-mono text-cyan-400">{approval.tool}</span></p>
      <p className="text-xs text-zinc-400 mt-1">{approval.reason || JSON.stringify(approval.args)}</p>
      <div className="mt-3 flex gap-2">
        <button onClick={() => onRespond(approval.approval_id, "approve")} className="rounded bg-cyan-500 px-3 py-1 text-xs text-black">执行一次</button>
        <button onClick={() => onRespond(approval.approval_id, "approve_similar")} className="rounded border border-cyan-500 px-3 py-1 text-xs text-cyan-400">同类均执行</button>
        <button onClick={() => onRespond(approval.approval_id, "reject")} className="rounded bg-zinc-800 px-3 py-1 text-xs text-zinc-300">拒绝</button>
      </div>
    </div>
  );
}
