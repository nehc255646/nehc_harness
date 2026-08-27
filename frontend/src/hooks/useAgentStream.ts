/** 处理流式事件 — 薄封装，实际逻辑在 store/agentStore */

import { useAgentStore } from "../store/agentStore";

export function useAgentStream() {
  const { messages, toolCalls, pendingApprovals, sendMessage, respondApproval, connectionState } = useAgentStore();
  return { messages, toolCalls, pendingApprovals, sendMessage, respondApproval, connectionState };
}
