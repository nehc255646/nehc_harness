import { create } from "zustand";
import { wsClient } from "../api/ws";

type Message = { id: string; role: string; content: string; streaming?: boolean };
type ToolCall = { call_id: string; name: string; args: unknown; result?: unknown };
type Approval = { approval_id: string; tool: string; args: unknown; reason: string };
type SubPanelMessage = { id: string; role: string; content: string; streaming?: boolean };
type SubPanel = {
  subagent_id: string;
  kind: string;
  task: string;
  status: string;
  result?: string;
  late?: boolean;
  messages: SubPanelMessage[];
};
type WorkerItem = { subagent_id: string; task_summary: string; state: string; batch_id?: string; late?: boolean };
type AgentState = "idle" | "running" | "awaiting_approval" | "done" | "error";

type State = {
  messages: Message[];
  toolCalls: ToolCall[];
  pendingApprovals: Approval[];
  sessionAllowRules: { kind: string; pattern: string }[];
  connectionState: "connecting" | "connected" | "disconnected" | "error";
  agentState: AgentState;
  sessionId: string;
  sessions: string[];
  subPanels: SubPanel[];
  dismissedPanels: string[];
  workers: WorkerItem[];
  sendMessage: (content: string) => void;
  sendSubagentMessage: (subagent_id: string, content: string) => void;
  respondApproval: (approval_id: string, decision: "approve" | "approve_similar" | "reject") => void;
  dismissPanel: (subagent_id: string) => void;
  stopWorker: (subagent_id: string) => void;
  connect: (sessionId?: string) => void;
};

// 事件 handler 只注册一次（StrictMode/重复 connect 下不重复绑定）
let handlersBound = false;

// 子 agent 流式消息归属：message_id -> subagent_id
const subMessageOwner = new Map<string, string>();

function updateSubPanelMessages(
  panels: SubPanel[],
  subagentId: string,
  fn: (msgs: SubPanelMessage[]) => SubPanelMessage[],
): SubPanel[] {
  return panels.map((x) => (x.subagent_id === subagentId ? { ...x, messages: fn(x.messages || []) } : x));
}

function bindHandlers(set: (partial: Partial<State> | ((s: State) => Partial<State>)) => void, get: () => State) {
  if (handlersBound) return;
  handlersBound = true;

  wsClient.on("connection", (p) => set({ connectionState: p.state as State["connectionState"] }));

  wsClient.on("session.hello", (p) => {
    // 断线恢复对账 — PLAN.md §2.5；新会话 (session 变化) 时清空展示
    const sid2 = p.session_id as string;
    wsClient.setSession(sid2);
    set((s) => {
      const incomingPanels = Array.isArray(p.subagent_panels) ? (p.subagent_panels as SubPanel[]) : s.subPanels;
      // 重连对账：保留本地已有面板的对话记录
      const merged = incomingPanels.map((np) => {
        const old = s.subPanels.find((x) => x.subagent_id === np.subagent_id);
        return { ...np, messages: old?.messages || [] };
      });
      return {
        sessionId: sid2,
        sessions: s.sessions.includes(sid2) ? s.sessions : [...s.sessions, sid2],
        messages: sid2 === s.sessionId ? s.messages : [],
        toolCalls: sid2 === s.sessionId ? s.toolCalls : [],
        pendingApprovals: Array.isArray(p.pending_approvals) ? (p.pending_approvals as Approval[]) : [],
        sessionAllowRules: Array.isArray(p.session_allow_rules) ? (p.session_allow_rules as State["sessionAllowRules"]) : [],
        subPanels: merged,
        workers: Array.isArray(p.workers) ? (p.workers as WorkerItem[]) : s.workers,
        agentState: (p.agent_state as AgentState) || s.agentState,
      };
    });
  });

  wsClient.on("session.update", (p) => {
    if (Array.isArray(p.session_allow_rules)) {
      set({ sessionAllowRules: p.session_allow_rules as State["sessionAllowRules"] });
    }
  });

  wsClient.on("agent.state", (p) => {
    set({ agentState: (p.state as AgentState) || "idle" });
  });

  wsClient.on("message.start", (p) => {
    const id = p.message_id as string;
    const subagentId = p.subagent_id as string | undefined;
    if (subagentId) {
      // 子 agent 流式 → 侧栏面板
      subMessageOwner.set(id, subagentId);
      set((s) => ({
        subPanels: updateSubPanelMessages(s.subPanels, subagentId, (msgs) => [
          ...msgs,
          { id, role: p.role as string, content: "", streaming: true },
        ]),
      }));
      return;
    }
    set((s) => ({ messages: [...s.messages, { id, role: p.role as string, content: "", streaming: true }] }));
  });

  wsClient.on("message.delta", (p) => {
    const id = p.message_id as string;
    const delta = p.delta as string;
    const subagentId = subMessageOwner.get(id);
    if (subagentId) {
      set((s) => ({
        subPanels: updateSubPanelMessages(s.subPanels, subagentId, (msgs) =>
          msgs.map((m) => (m.id === id ? { ...m, content: m.content + delta } : m)),
        ),
      }));
      return;
    }
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, content: m.content + delta } : m)),
    }));
  });

  wsClient.on("message.done", (p) => {
    const id = p.message_id as string;
    const subagentId = subMessageOwner.get(id);
    if (subagentId) {
      set((s) => ({
        subPanels: updateSubPanelMessages(s.subPanels, subagentId, (msgs) =>
          msgs.map((m) => (m.id === id ? { ...m, content: p.content as string, streaming: false } : m)),
        ),
      }));
      return;
    }
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, content: p.content as string, streaming: false } : m)),
    }));
    // 若 message.done 没有对应 start，直接追加
    const exists = get().messages.some((m) => m.id === id);
    if (!exists) {
      set((s) => ({ messages: [...s.messages, { id, role: p.role as string, content: p.content as string }] }));
    }
  });

  wsClient.on("tool.start", (p) => set((s) => ({ toolCalls: [...s.toolCalls, { call_id: p.call_id as string, name: p.name as string, args: p.args }] })));
  wsClient.on("tool.result", (p) =>
    set((s) => ({ toolCalls: s.toolCalls.map((t) => (t.call_id === p.call_id ? { ...t, result: p.result } : t)) })),
  );
  wsClient.on("approval.request", (p) =>
    set((s) => ({ pendingApprovals: [...s.pendingApprovals, p as unknown as Approval] })),
  );
  wsClient.on("approval.resolved", (p) =>
    set((s) => ({ pendingApprovals: s.pendingApprovals.filter((a) => a.approval_id !== p.approval_id) })),
  );
  wsClient.on("subagent.opened", (p) => {
    const panel = p as unknown as SubPanel;
    // interactive 侧栏，worker 忽略（由 worker.status 展示）
    if (panel.kind === "interactive") {
      set((s) => ({
        subPanels: [
          ...s.subPanels.filter((x) => x.subagent_id !== panel.subagent_id),
          { ...panel, messages: [] },
        ],
      }));
    }
  });
  wsClient.on("subagent.done", (p) => {
    const sid = p.subagent_id as string;
    const result = p.result_summary as string;
    set((s) => ({
      subPanels: s.subPanels.map((x) => (x.subagent_id === sid ? { ...x, status: "done", result } : x)),
    }));
  });
  wsClient.on("worker.status", (p) => {
    const workers = (p.workers as WorkerItem[]) || [];
    set({ workers });
  });
  wsClient.on("worker.batch_done", (p) => {
    // 批量完成已通过主 agent 消息注入上下文，此处仅刷新 workers
    const batchId = p.batch_id as string;
    set((s) => ({
      workers: s.workers.map((w) => (w.batch_id === batchId ? { ...w, state: "done" } : w)),
    }));
  });
  wsClient.on("subagent.message", (p) => {
    // 用户侧栏输入的服务端回显 → 面板对话记录
    const sid = p.subagent_id as string;
    const content = p.content as string;
    const role = p.role as string;
    set((s) => ({
      subPanels: updateSubPanelMessages(s.subPanels, sid, (msgs) => [
        ...msgs,
        { id: `sub-${sid}-${Date.now()}`, role, content },
      ]),
    }));
  });
}

export const useAgentStore = create<State>((set, get) => ({
  messages: [],
  toolCalls: [],
  pendingApprovals: [],
  sessionAllowRules: [],
  connectionState: "connecting",
  agentState: "idle",
  sessionId: "default",
  sessions: ["default"],
  subPanels: [],
  dismissedPanels: [],
  workers: [],

  connect: (sessionId) => {
    const sid = sessionId || get().sessionId;
    set({ sessionId: sid, connectionState: "connecting" });
    bindHandlers(set, get);
    wsClient.connect(sid);
  },

  sendMessage: (content) => {
    const sid = get().sessionId;
    const localId = `local-${Date.now()}`;
    // 仅在开启新 run 时清空上一轮工具卡，运行中追加消息不抹卡片
    const fresh = get().agentState === "idle" || get().agentState === "done" || get().agentState === "error";
    set((s) => ({ messages: [...s.messages, { id: localId, role: "user", content }], toolCalls: fresh ? [] : s.toolCalls }));
    wsClient.send("message.send", { session_id: sid, content });
  },

  respondApproval: (approval_id, decision) => {
    wsClient.send("approval.response", { approval_id, decision });
  },
  sendSubagentMessage: (subagent_id, content) => {
    const sid = get().sessionId;
    // 回显由服务端 subagent.message 广播统一处理（含发送者本人），避免重复气泡
    wsClient.send("subagent.response", { session_id: sid, subagent_id, content });
  },
  dismissPanel: (subagent_id) => {
    // 仅收起 UI，不终止子 agent（PLAN §2.4）
    set((s) => ({
      dismissedPanels: s.dismissedPanels.includes(subagent_id) ? s.dismissedPanels : [...s.dismissedPanels, subagent_id],
    }));
  },
  stopWorker: (subagent_id) => {
    wsClient.send("agent.stop", { agent_id: subagent_id });
  },
}));
