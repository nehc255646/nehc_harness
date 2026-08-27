import { create } from "zustand";
import { wsClient } from "../api/ws";

type Message = { id: string; role: string; content: string; streaming?: boolean };
type ToolCall = { call_id: string; name: string; args: unknown; result?: unknown };
type Approval = { approval_id: string; tool: string; args: unknown; reason: string };

type State = {
  messages: Message[];
  toolCalls: ToolCall[];
  pendingApprovals: Approval[];
  connectionState: "connecting" | "connected" | "disconnected" | "error";
  sessionId: string;
  sendMessage: (content: string) => void;
  respondApproval: (approval_id: string, decision: "approve" | "approve_similar" | "reject") => void;
  connect: (sessionId?: string) => void;
};

export const useAgentStore = create<State>((set, get) => ({
  messages: [],
  toolCalls: [],
  pendingApprovals: [],
  connectionState: "connecting",
  sessionId: "default",

  connect: (sessionId) => {
    const sid = sessionId || get().sessionId;
    set({ sessionId: sid, connectionState: "connecting" });

    wsClient.connect(sid);

    wsClient.on("connection", (p) => set({ connectionState: p.state as State["connectionState"] }));

    wsClient.on("session.hello", (p) => {
      // 断线恢复对账 — PLAN.md §2.5
      if (Array.isArray(p.pending_approvals)) set({ pendingApprovals: p.pending_approvals as Approval[] });
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
  },

  sendMessage: (content) => {
    const sid = get().sessionId;
    const localId = `local-${Date.now()}`;
    set((s) => ({ messages: [...s.messages, { id: localId, role: "user", content }] }));
    wsClient.send("message.send", { session_id: sid, content });
  },

  respondApproval: (approval_id, decision) => {
    wsClient.send("approval.response", { approval_id, decision });
  },
}));
