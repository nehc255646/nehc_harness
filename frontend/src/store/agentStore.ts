import { create } from "zustand";
import { wsClient } from "../api/ws";

type Message = { id: string; role: string; content: string; streaming?: boolean; subagent_id?: string };
type ToolCall = { call_id: string; name: string; args: unknown; result?: unknown };
type Approval = { approval_id: string; tool: string; args: unknown; reason: string };
type SubPanel = { subagent_id: string; kind: string; task: string; status: string; result?: string };
type WorkerItem = { subagent_id: string; task_summary: string; state: string; batch_id?: string };

type State = {
  messages: Message[];
  toolCalls: ToolCall[];
  pendingApprovals: Approval[];
  sessionAllowRules: { kind: string; pattern: string }[];
  connectionState: "connecting" | "connected" | "disconnected" | "error";
  sessionId: string;
  sessions: string[];
  subPanels: SubPanel[];
  workers: WorkerItem[];
  sendMessage: (content: string) => void;
  sendSubagentMessage: (subagent_id: string, content: string) => void;
  respondApproval: (approval_id: string, decision: "approve" | "approve_similar" | "reject") => void;
  connect: (sessionId?: string) => void;
};

export const useAgentStore = create<State>((set, get) => ({
  messages: [],
  toolCalls: [],
  pendingApprovals: [],
  sessionAllowRules: [],
  connectionState: "connecting",
  sessionId: "default",
  sessions: ["default"],
  subPanels: [],
  workers: [],

  connect: (sessionId) => {
    const sid = sessionId || get().sessionId;
    set({ sessionId: sid, connectionState: "connecting" });

    wsClient.connect(sid);

    wsClient.on("connection", (p) => set({ connectionState: p.state as State["connectionState"] }));

    wsClient.on("session.hello", (p) => {
      // 断线恢复对账 — PLAN.md §2.5；新会话 (session 变化) 时清空展示
      const sid2 = p.session_id as string;
      set((s) => ({
        sessionId: sid2,
        sessions: s.sessions.includes(sid2) ? s.sessions : [...s.sessions, sid2],
        messages: sid2 === s.sessionId ? s.messages : [],
        toolCalls: sid2 === s.sessionId ? s.toolCalls : [],
        pendingApprovals: Array.isArray(p.pending_approvals) ? (p.pending_approvals as Approval[]) : [],
        sessionAllowRules: Array.isArray(p.session_allow_rules) ? (p.session_allow_rules as State["sessionAllowRules"]) : [],
        subPanels: Array.isArray(p.subagent_panels) ? (p.subagent_panels as SubPanel[]) : s.subPanels,
        workers: Array.isArray(p.workers) ? (p.workers as WorkerItem[]) : s.workers,
      }));
    });

    wsClient.on("session.update", (p) => {
      if (Array.isArray(p.session_allow_rules)) {
        set({ sessionAllowRules: p.session_allow_rules as State["sessionAllowRules"] });
      }
    });

    wsClient.on("message.start", (p) => {
      const id = p.message_id as string;
      set((s) => ({ messages: [...s.messages, { id, role: p.role as string, content: "", streaming: true }] }));
    });

    wsClient.on("message.delta", (p) => {
      const id = p.message_id as string;
      const delta = p.delta as string;
      set((s) => ({
        messages: s.messages.map((m) => (m.id === id ? { ...m, content: m.content + delta } : m)),
      }));
    });

    wsClient.on("message.done", (p) => {
      const id = p.message_id as string;
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
        set((s) => ({ subPanels: [...s.subPanels.filter((x) => x.subagent_id !== panel.subagent_id), panel as SubPanel] }));
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
      const sid = p.subagent_id as string;
      const content = p.content as string;
      const role = p.role as string;
      set((s) => ({
        messages: [...s.messages, { id: `sub-${sid}-${Date.now()}`, role: role === "user" ? "user(sub)" : "assistant(sub)", content: `[${sid}] ${content}` }],
      }));
    });
  },

  sendMessage: (content) => {
    const sid = get().sessionId;
    const localId = `local-${Date.now()}`;
    // 新 run 开始，清空上一轮的 tool 卡片
    set((s) => ({ messages: [...s.messages, { id: localId, role: "user", content }], toolCalls: [] }));
    wsClient.send("message.send", { session_id: sid, content });
  },

  respondApproval: (approval_id, decision) => {
    wsClient.send("approval.response", { approval_id, decision });
  },
  sendSubagentMessage: (subagent_id, content) => {
    const sid = get().sessionId;
    wsClient.send("subagent.response", { session_id: sid, subagent_id, content });
    // 本地回显
    set((s) => ({ messages: [...s.messages, { id: `local-sub-${Date.now()}`, role: "user(sub)", content: `[->${subagent_id}] ${content}` }] }));
  },
}));
