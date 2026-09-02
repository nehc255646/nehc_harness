import { create } from "zustand";
import {
  extractDiff,
  historyToChat,
  mergeChatMessages,
  rest,
  type FileDiffPayload,
  type ModelRow,
  type SessionRow,
  type ToolLogRow,
  type WorkMode,
} from "../api/rest";
import { wsClient } from "../api/ws";

type Message = {
  id: string;
  role: string;
  content: string;
  thinking?: string;
  thinkingStreaming?: boolean;
  streaming?: boolean;
};
type ToolCall = {
  call_id: string;
  name: string;
  args: unknown;
  result?: unknown;
  diff?: FileDiffPayload;
  progress?: string;
  messageId?: string;
};
type Approval = { approval_id: string; tool: string; args: unknown; reason: string };
type SubPanelMessage = {
  id: string;
  role: string;
  content: string;
  thinking?: string;
  thinkingStreaming?: boolean;
  streaming?: boolean;
};
type SubPanel = {
  subagent_id: string;
  kind: string;
  task: string;
  status: string;
  result?: string;
  late?: boolean;
  messages: SubPanelMessage[];
};
type WorkerItem = {
  subagent_id: string;
  task_summary: string;
  state: string;
  batch_id?: string;
  late?: boolean;
  result?: string;
};
type AgentState = "idle" | "running" | "awaiting_approval" | "awaiting_workers" | "done" | "error";

type State = {
  messages: Message[];
  toolCalls: ToolCall[];
  pendingApprovals: Approval[];
  sessionAllowRules: { kind: string; pattern: string }[];
  connectionState: "connecting" | "connected" | "disconnected" | "error";
  agentState: AgentState;
  sessionId: string;
  sessionTitle: string;
  modelId: number | null;
  workMode: WorkMode;
  sessionRows: SessionRow[];
  models: ModelRow[];
  sendBlockedReason: string;
  subPanels: SubPanel[];
  dismissedPanels: string[];
  subPanelOpen: boolean;
  workers: WorkerItem[];
  sendMessage: (content: string) => void;
  sendSubagentMessage: (subagent_id: string, content: string) => void;
  respondApproval: (approval_id: string, decision: "approve" | "approve_similar" | "reject") => void;
  revokeAllowRule: (kind: string, pattern: string) => void;
  dismissPanel: (subagent_id: string) => void;
  restorePanels: () => void;
  setSubPanelOpen: (open: boolean) => void;
  toggleSubAgent: () => void;
  startInteractive: (content?: string) => void;
  stopWorker: (subagent_id: string) => void;
  connect: (sessionId?: string) => void;
  boot: () => Promise<void>;
  refreshSessions: () => Promise<void>;
  refreshModels: () => Promise<void>;
  setSessionModel: (modelId: number | null) => Promise<void>;
  setSessionWorkMode: (mode: WorkMode) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  stopAgent: () => void;
  selectSession: (id: string) => void;
  createSession: () => void;
  stopInteractive: (subagent_id: string) => void;
};

// 事件 handler 只注册一次（StrictMode/重复 connect 下不重复绑定）
let handlersBound = false;
let bootStarted = false;
let pendingHello: string | "any" | null = null;

// 子 agent 流式消息归属：message_id -> subagent_id
const subMessageOwner = new Map<string, string>();

function updateSubPanelMessages(
  panels: SubPanel[],
  subagentId: string,
  fn: (msgs: SubPanelMessage[]) => SubPanelMessage[],
): SubPanel[] {
  const ensured = panels.some((x) => x.subagent_id === subagentId)
    ? panels
    : [
        ...panels,
        { subagent_id: subagentId, kind: "interactive", task: "", status: "running", messages: [] as SubPanelMessage[] },
      ];
  return ensured.map((x) => (x.subagent_id === subagentId ? { ...x, messages: fn(x.messages || []) } : x));
}

function rememberSubOwner(messageId: string, subagentId: string) {
  if (subMessageOwner.size > 500) {
    const first = subMessageOwner.keys().next().value;
    if (first) subMessageOwner.delete(first);
  }
  subMessageOwner.set(messageId, subagentId);
}

function resetSessionView(set: (partial: Partial<State>) => void, id: string, title?: string) {
  subMessageOwner.clear();
  set({
    sessionId: id,
    sessionTitle: title || "",
    messages: [],
    toolCalls: [],
    pendingApprovals: [],
    subPanels: [],
    workers: [],
    dismissedPanels: [],
    agentState: "idle",
    sendBlockedReason: "",
  });
}

function bindHandlers(set: (partial: Partial<State> | ((s: State) => Partial<State>)) => void, get: () => State) {
  if (handlersBound) return;
  handlersBound = true;

  wsClient.on("connection", (p) => set({ connectionState: p.state as State["connectionState"] }));

  wsClient.on("session.hello", (p) => {
    const sid2 = p.session_id as string;
    if (pendingHello === "any") {
      pendingHello = null;
    } else if (pendingHello && sid2 !== pendingHello) {
      return;
    } else if (get().sessionId && sid2 !== get().sessionId) {
      return;
    }
    pendingHello = null;
    wsClient.setSession(sid2);
    subMessageOwner.clear();
    set((s) => {
      const incomingPanels = Array.isArray(p.subagent_panels) ? (p.subagent_panels as SubPanel[]) : s.subPanels;
      const merged = incomingPanels.map((np) => {
        const old = s.subPanels.find((x) => x.subagent_id === np.subagent_id);
        const incomingMsgs = Array.isArray(np.messages) ? np.messages : [];
        return { ...np, messages: incomingMsgs.length ? incomingMsgs : old?.messages || [] };
      });
      const switched = sid2 !== s.sessionId;
      const live =
        p.agent_state === "running" ||
        p.agent_state === "awaiting_approval" ||
        p.agent_state === "awaiting_workers";
      return {
        sessionId: sid2,
        sessionTitle: (p.title as string) || s.sessionTitle,
        modelId: (p.model_id as number | null) ?? null,
        workMode: p.work_mode === "plan" ? "plan" : "auto",
        messages: switched
          ? []
          : s.messages.map((m) => ({ ...m, streaming: Boolean(m.streaming && live) })),
        toolCalls: switched ? [] : s.toolCalls,
        pendingApprovals: Array.isArray(p.pending_approvals) ? (p.pending_approvals as Approval[]) : [],
        sessionAllowRules: Array.isArray(p.session_allow_rules) ? (p.session_allow_rules as State["sessionAllowRules"]) : [],
        subPanels: merged,
        dismissedPanels: switched ? [] : s.dismissedPanels,
        workers: Array.isArray(p.workers) ? (p.workers as WorkerItem[]) : s.workers,
        agentState: (p.agent_state as AgentState) || s.agentState,
        subPanelOpen: switched
          ? Boolean(
              merged.some((x) => x.status === "running") ||
                (Array.isArray(p.workers) && (p.workers as WorkerItem[]).some((w) => w.state === "running")),
            )
          : s.subPanelOpen,
      };
    });
    // 从 REST 拉历史与工具日志，工具挂到对应 assistant 消息上
    void Promise.allSettled([rest.messages(sid2), rest.toolLogs(sid2)]).then((results) => {
      const rows = results[0].status === "fulfilled" ? results[0].value : [];
      const logs: ToolLogRow[] = results[1].status === "fulfilled" ? results[1].value : [];
      set((s) => {
        if (s.sessionId !== sid2) return {};
        const patch: Partial<State> = {};
        if (results[0].status === "fulfilled") {
          patch.messages = mergeChatMessages(historyToChat(rows), s.messages);
        }
        const pkToPublic = new Map(rows.map((r) => [r.id, r.public_id]));
        const fromRest: ToolCall[] = logs
          .filter((r) => r.agent_id === "main")
          .map((r) => ({
            call_id: r.tool_call_id,
            name: r.name,
            args: r.args,
            result:
              r.result && typeof r.result === "object" && r.result !== null && "text" in r.result
                ? (r.result as { text?: unknown }).text
                : r.result,
            diff: extractDiff(r.name, r.args, r.result),
            messageId: (r.message_id != null ? pkToPublic.get(r.message_id) : undefined) || undefined,
          }));
        const restIds = new Set(fromRest.map((t) => t.call_id));
        const liveById = new Map(s.toolCalls.map((t) => [t.call_id, t]));
        const merged: ToolCall[] = fromRest.map((t) => {
          const live = liveById.get(t.call_id);
          if (!live) return t;
          return {
            ...t,
            ...live,
            result: live.result !== undefined ? live.result : t.result,
            diff: live.diff || t.diff,
            progress: live.result !== undefined ? undefined : live.progress,
            messageId: live.messageId || t.messageId,
          };
        });
        for (const t of s.toolCalls) {
          if (!restIds.has(t.call_id)) merged.push(t);
        }
        patch.toolCalls = merged;
        return patch;
      });
    });
    rest
      .sessions()
      .then((rows) => set({ sessionRows: rows }))
      .catch(() => {
        /* ignore */
      });
  });

  wsClient.on("session.update", (p) => {
    const patch: Partial<State> = {};
    if (Array.isArray(p.session_allow_rules)) {
      patch.sessionAllowRules = p.session_allow_rules as State["sessionAllowRules"];
    }
    if (typeof p.title === "string") {
      patch.sessionTitle = p.title;
      const sid = (p.session_id as string) || get().sessionId;
      patch.sessionRows = get().sessionRows.map((r) => (r.id === sid ? { ...r, title: p.title as string } : r));
    }
    if (Object.keys(patch).length) set(patch);
  });

  wsClient.on("agent.state", (p) => {
    set({ agentState: (p.state as AgentState) || "idle" });
  });

  wsClient.on("message.start", (p) => {
    const id = p.message_id as string;
    const subagentId = p.subagent_id as string | undefined;
    if (subagentId) {
      rememberSubOwner(id, subagentId);
      set((s) => ({
        subPanels: updateSubPanelMessages(s.subPanels, subagentId, (msgs) => [
          ...msgs,
          { id, role: p.role as string, content: "", thinking: "", streaming: true },
        ]),
      }));
      return;
    }
    set((s) => ({
      messages: [...s.messages, { id, role: p.role as string, content: "", thinking: "", streaming: true }],
    }));
  });

  wsClient.on("message.delta", (p) => {
    const id = p.message_id as string;
    const delta = (p.delta as string) || "";
    const thinkingCh = p.channel === "thinking";
    const subagentId = (p.subagent_id as string | undefined) || subMessageOwner.get(id);
    if (subagentId) rememberSubOwner(id, subagentId);
    const patchMsg = <T extends { id: string; content: string; thinking?: string; thinkingStreaming?: boolean }>(
      m: T,
    ): T => {
      if (m.id !== id) return m;
      if (thinkingCh) {
        return { ...m, thinking: (m.thinking || "") + delta, thinkingStreaming: true };
      }
      return { ...m, content: m.content + delta, thinkingStreaming: false };
    };
    if (subagentId) {
      set((s) => ({
        subPanels: updateSubPanelMessages(s.subPanels, subagentId, (msgs) => {
          if (msgs.some((m) => m.id === id)) return msgs.map(patchMsg);
          return [
            ...msgs,
            thinkingCh
              ? { id, role: "assistant", content: "", thinking: delta, thinkingStreaming: true, streaming: true }
              : { id, role: "assistant", content: delta, thinking: "", streaming: true },
          ];
        }),
      }));
      return;
    }
    set((s) => {
      if (s.messages.some((m) => m.id === id)) {
        return { messages: s.messages.map(patchMsg) };
      }
      return {
        messages: [
          ...s.messages,
          thinkingCh
            ? { id, role: "assistant", content: "", thinking: delta, thinkingStreaming: true, streaming: true }
            : { id, role: "assistant", content: delta, thinking: "", streaming: true },
        ],
      };
    });
  });

  wsClient.on("message.done", (p) => {
    const id = p.message_id as string;
    const thinking = typeof p.thinking === "string" ? p.thinking : undefined;
    const subagentId = (p.subagent_id as string | undefined) || subMessageOwner.get(id);
    subMessageOwner.delete(id);
    if (subagentId) {
      set((s) => ({
        subPanels: updateSubPanelMessages(s.subPanels, subagentId, (msgs) =>
          msgs.map((m) =>
            m.id === id
              ? {
                  ...m,
                  content: p.content as string,
                  thinking: thinking ?? m.thinking,
                  thinkingStreaming: false,
                  streaming: false,
                }
              : m,
          ),
        ),
      }));
      return;
    }
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id
          ? {
              ...m,
              content: p.content as string,
              thinking: thinking ?? m.thinking,
              thinkingStreaming: false,
              streaming: false,
            }
          : m,
      ),
    }));
    // 若 message.done 没有对应 start，直接追加
    const exists = get().messages.some((m) => m.id === id);
    if (!exists) {
      set((s) => ({
        messages: [
          ...s.messages,
          { id, role: p.role as string, content: p.content as string, thinking: thinking || "" },
        ],
      }));
    }
  });

  wsClient.on("tool.start", (p) => {
    // 工作型子 agent 工具不进主聊天（PLAN §2.4：工作区仅列表，无详细日志）
    if (p.subagent_id) return;
    set((s) => {
      const incoming: ToolCall = {
        call_id: p.call_id as string,
        name: p.name as string,
        args: p.args,
        messageId: typeof p.message_id === "string" ? p.message_id : undefined,
      };
      const mid = incoming.messageId;
      const messages =
        mid && s.messages.some((m) => m.id === mid && m.thinkingStreaming)
          ? s.messages.map((m) => (m.id === mid ? { ...m, thinkingStreaming: false } : m))
          : s.messages;
      const idx = s.toolCalls.findIndex((t) => t.call_id === incoming.call_id);
      if (idx >= 0) {
        const copy = s.toolCalls.slice();
        copy[idx] = { ...copy[idx], ...incoming, args: incoming.args ?? copy[idx].args };
        return { toolCalls: copy, messages };
      }
      return { toolCalls: [...s.toolCalls, incoming], messages };
    });
  });
  wsClient.on("tool.result", (p) => {
    // 与 tool.start 一致：工作型子 agent 工具结果不进主聊天
    if (p.subagent_id) return;
    set((s) => ({
      toolCalls: s.toolCalls.map((t) =>
        t.call_id === p.call_id
          ? {
              ...t,
              result: p.result,
              progress: undefined,
              diff: (p.diff as FileDiffPayload | undefined) || extractDiff(t.name, t.args, p.result) || t.diff,
            }
          : t,
      ),
    }));
  });
  wsClient.on("tool.progress", (p) => {
    if (p.subagent_id) return;
    set((s) => ({
      toolCalls: s.toolCalls.map((t) => (t.call_id === p.call_id ? { ...t, progress: String(p.tail || "") } : t)),
    }));
  });
  wsClient.on("approval.request", (p) =>
    set((s) => {
      const next = p as unknown as Approval;
      if (s.pendingApprovals.some((a) => a.approval_id === next.approval_id)) return {};
      return { pendingApprovals: [...s.pendingApprovals, next] };
    }),
  );
  wsClient.on("approval.resolved", (p) =>
    set((s) => ({ pendingApprovals: s.pendingApprovals.filter((a) => a.approval_id !== p.approval_id) })),
  );
  wsClient.on("error", (p) => {
    const message = typeof p.message === "string" ? p.message : JSON.stringify(p);
    if (message.includes("已处理或不存在")) return;
    set((s) => ({
      messages: [...s.messages, { id: `err-${Date.now()}`, role: "assistant", content: `[错误] ${message}` }],
    }));
  });
  wsClient.on("session.deleted", (p) => {
    const deletedId = p.session_id as string;
    if (deletedId !== get().sessionId) return;
    rest
      .sessions()
      .then(async (rows) => {
        set({ sessionRows: rows });
        let next = rows[0];
        if (!next) {
          next = await rest.createSession("New Session");
          set({ sessionRows: [next] });
        }
        get().selectSession(next.id);
      })
      .catch(() => {
        /* ignore */
      });
  });
  wsClient.on("subagent.opened", (p) => {
    const panel = p as unknown as SubPanel;
    if (panel.kind === "interactive") {
      set((s) => ({
        subPanels: [
          ...s.subPanels.filter((x) => x.subagent_id !== panel.subagent_id),
          { ...panel, status: panel.status || "running", messages: [] },
        ],
        dismissedPanels: s.dismissedPanels.filter((id) => id !== panel.subagent_id),
        subPanelOpen: true,
      }));
    } else if (panel.kind === "worker") {
      set({ subPanelOpen: true });
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
    set((s) => ({
      workers,
      subPanelOpen: s.subPanelOpen || workers.some((w) => w.state === "running"),
    }));
  });
  wsClient.on("worker.batch_done", (p) => {
    const batchId = p.batch_id as string;
    const done = (p.workers as { subagent_id?: string; status?: string; result?: string }[]) || [];
    set((s) => ({
      workers: s.workers.map((w) => {
        const hit = done.find((d) => d.subagent_id === w.subagent_id);
        if (hit) {
          return { ...w, state: hit.status || "done", result: hit.result ?? w.result };
        }
        return w.batch_id === batchId ? { ...w, state: "done" } : w;
      }),
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
  sessionId: "",
  sessionTitle: "",
  modelId: null,
  workMode: "auto",
  sessionRows: [],
  models: [],
  sendBlockedReason: "",
  subPanels: [],
  dismissedPanels: [],
  subPanelOpen: false,
  workers: [],

  connect: (sessionId) => {
    const sid = sessionId || get().sessionId;
    pendingHello = sid;
    set({ sessionId: sid, connectionState: "connecting" });
    bindHandlers(set, get);
    wsClient.connect(sid);
  },

  boot: async () => {
    if (bootStarted) return;
    bootStarted = true;
    bindHandlers(set, get);
    try {
      const [rows, models] = await Promise.all([rest.sessions(), rest.models().catch(() => [] as ModelRow[])]);
      set({ sessionRows: rows, models });
      let sid = rows[0]?.id;
      if (!sid) {
        const created = await rest.createSession("New Session");
        sid = created.id;
        set({ sessionRows: [created] });
      }
      pendingHello = sid;
      set({ sessionId: sid, connectionState: "connecting" });
      wsClient.connect(sid);
    } catch {
      const sid = get().sessionId || "default";
      pendingHello = sid;
      set({ sessionId: sid, connectionState: "connecting" });
      wsClient.connect(sid);
    }
  },

  refreshSessions: async () => {
    try {
      set({ sessionRows: await rest.sessions() });
    } catch {
      /* ignore */
    }
  },

  refreshModels: async () => {
    try {
      set({ models: await rest.models() });
    } catch {
      set({ models: [] });
    }
  },

  setSessionModel: async (modelId) => {
    const sid = get().sessionId;
    if (!sid) return;
    const row = await rest.patchSession(sid, { model_id: modelId });
    set({ modelId: row.model_id, sendBlockedReason: "" });
    await get().refreshSessions();
  },

  setSessionWorkMode: async (mode) => {
    const sid = get().sessionId;
    if (!sid) return;
    const row = await rest.patchSession(sid, { work_mode: mode });
    const next: WorkMode = row.work_mode === "plan" ? "plan" : "auto";
    set((s) => ({
      workMode: next,
      sessionRows: s.sessionRows.map((r) => (r.id === sid ? { ...r, work_mode: next } : r)),
    }));
  },

  renameSession: async (id, title) => {
    const row = await rest.patchSession(id, { title });
    set((s) => ({
      sessionRows: s.sessionRows.map((r) => (r.id === id ? row : r)),
      sessionTitle: s.sessionId === id ? row.title : s.sessionTitle,
    }));
  },

  stopAgent: () => {
    const sid = get().sessionId;
    if (sid) wsClient.send("agent.stop", { agent_id: sid });
  },

  stopInteractive: (subagent_id) => {
    if (subagent_id) wsClient.send("agent.stop", { agent_id: subagent_id });
  },

  selectSession: (id) => {
    if (!id || id === get().sessionId) return;
    pendingHello = id;
    wsClient.setSession(id);
    resetSessionView(set, id, get().sessionRows.find((r) => r.id === id)?.title);
    wsClient.send("session.select", { session_id: id });
  },

  createSession: () => {
    pendingHello = "any";
    subMessageOwner.clear();
    set({
      messages: [],
      toolCalls: [],
      pendingApprovals: [],
      subPanels: [],
      workers: [],
      dismissedPanels: [],
      agentState: "idle",
    });
    wsClient.send("session.create", { title: "New Session" });
  },

  deleteSession: async (id) => {
    try {
      await rest.deleteSession(id);
      const rows = await rest.sessions();
      set({ sessionRows: rows });
      if (get().sessionId === id) {
        const next = rows[0];
        if (next) {
          get().selectSession(next.id);
        } else {
          const created = await rest.createSession("New Session");
          set({ sessionRows: [created] });
          get().selectSession(created.id);
        }
      }
    } catch (e) {
      set({ sendBlockedReason: `删除失败: ${String(e)}` });
    }
  },

  sendMessage: (content) => {
    const { sessionId: sid, models, modelId } = get();
    if (models.length > 0 && !modelId) {
      set({ sendBlockedReason: "请先选择模型后再发送" });
      return;
    }
    set({ sendBlockedReason: "" });
    const localId = `local-${Date.now()}`;
    set((s) => ({ messages: [...s.messages, { id: localId, role: "user", content }] }));
    wsClient.send("message.send", { session_id: sid, content });
  },

  respondApproval: (approval_id, decision) => {
    set((s) => ({ pendingApprovals: s.pendingApprovals.filter((a) => a.approval_id !== approval_id) }));
    wsClient.send("approval.response", { approval_id, decision });
  },
  revokeAllowRule: (kind, pattern) => {
    wsClient.send("session.allow_revoke", { kind, pattern });
    set((s) => ({
      sessionAllowRules: s.sessionAllowRules.filter((r) => !(r.kind === kind && r.pattern === pattern)),
    }));
  },
  sendSubagentMessage: (subagent_id, content) => {
    const sid = get().sessionId;
    // 回显由服务端 subagent.message 广播统一处理（含发送者本人），避免重复气泡
    wsClient.send("subagent.response", { session_id: sid, subagent_id, content });
  },
  dismissPanel: (subagent_id) => {
    // 仅收起 UI，不终止子 agent
    set((s) => ({
      dismissedPanels: s.dismissedPanels.includes(subagent_id) ? s.dismissedPanels : [...s.dismissedPanels, subagent_id],
    }));
  },
  restorePanels: () => set({ dismissedPanels: [], subPanelOpen: true }),
  setSubPanelOpen: (open) => set({ subPanelOpen: open }),
  startInteractive: (content) => {
    const s = get();
    set({ dismissedPanels: [], subPanelOpen: true });
    const text = (content || "").trim();
    const running = s.subPanels.find((p) => p.status === "running");
    if (running) {
      if (text) {
        wsClient.send("subagent.response", { session_id: s.sessionId, subagent_id: running.subagent_id, content: text });
      }
      return;
    }
    wsClient.send("subagent.open", { session_id: s.sessionId, content: text });
  },
  toggleSubAgent: () => {
    const s = get();
    if (s.subPanelOpen) {
      set({ subPanelOpen: false });
      return;
    }
    set({ dismissedPanels: [], subPanelOpen: true });
  },
  stopWorker: (subagent_id) => {
    wsClient.send("agent.stop", { agent_id: subagent_id });
  },
}));
