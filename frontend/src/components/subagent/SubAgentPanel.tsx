/** 交互型子 agent 侧栏面板 — M2 实现 */

export default function SubAgentPanel() {
  return (
    <aside className="w-80 border-l border-zinc-800 bg-zinc-950 p-3 hidden lg:block">
      <h3 className="text-xs font-semibold text-zinc-400">子 Agent 面板</h3>
      <p className="mt-2 text-xs text-zinc-600">交互型侧栏（M2）— 暂无活动会话</p>
    </aside>
  );
}
