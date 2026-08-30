import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAgentStore } from "../store/agentStore";
import ApprovalModal from "./ApprovalModal";
import { IconSend } from "./icons";
import ThinkingBlock from "./ThinkingBlock";
import ToolCallCard from "./ToolCallCard";

const EXAMPLES_AUTO = ["执行 echo hello", "列出当前工作目录", "写入 hello.txt，内容为 hello Neharness"];
const EXAMPLES_PLAN = ["阅读工作区结构", "说明当前代码如何启动", "给出下一步实现计划"];
const COL = "mx-auto w-full max-w-6xl px-4 sm:px-6";

export default function Chat() {
  const {
    messages,
    toolCalls,
    pendingApprovals,
    sendMessage,
    sendBlockedReason,
    models,
    modelId,
    sessionId,
    setSessionModel,
    setSessionWorkMode,
    workMode,
    respondApproval,
    agentState,
  } = useAgentStore();
  const [input, setInput] = useState("");
  const [pickedSession, setPickedSession] = useState(sessionId);
  const [pickedProviderId, setPickedProviderId] = useState<number | null>(null);
  const [pickedModelId, setPickedModelId] = useState<number | null>(null);
  const [pickerErr, setPickerErr] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  if (pickedSession !== sessionId) {
    setPickedSession(sessionId);
    setPickedProviderId(null);
    setPickedModelId(null);
    setInput("");
  }

  const providerOpts = useMemo(() => {
    const map = new Map<number, string>();
    for (const m of models) {
      if (!map.has(m.provider_id)) {
        map.set(m.provider_id, m.provider_name || m.provider_slug || `供应商 ${m.provider_id}`);
      }
    }
    return [...map.entries()].map(([id, name]) => ({ id, name }));
  }, [models]);

  const sessionModel = models.find((m) => m.id === modelId);
  let draftProviderId = pickedProviderId ?? sessionModel?.provider_id ?? models[0]?.provider_id ?? null;
  if (draftProviderId != null && !models.some((m) => m.provider_id === draftProviderId)) {
    draftProviderId = models[0]?.provider_id ?? null;
  }
  const modelsForProvider = models.filter((m) => m.provider_id === draftProviderId);
  let draftModelId = pickedModelId;
  if (draftModelId == null || !modelsForProvider.some((m) => m.id === draftModelId)) {
    draftModelId =
      (sessionModel && sessionModel.provider_id === draftProviderId ? sessionModel.id : null) ??
      modelsForProvider[0]?.id ??
      null;
  }
  const blocked = models.length > 0 && !draftModelId;

  const onProviderChange = (pid: number) => {
    setPickedProviderId(pid);
    const list = models.filter((m) => m.provider_id === pid);
    const keep = list.find((m) => m.id === pickedModelId);
    setPickedModelId(keep?.id ?? list[0]?.id ?? null);
  };

  const onSend = async () => {
    const text = input.trim();
    if (!text || blocked) return;
    setPickerErr("");
    if (models.length > 0) {
      if (!draftModelId) return;
      if (draftModelId !== modelId) {
        try {
          await setSessionModel(draftModelId);
        } catch (e) {
          setPickerErr(String(e));
          return;
        }
      }
    }
    sendMessage(text);
    setInput("");
    if (taRef.current) taRef.current.style.height = "auto";
  };

  useEffect(() => {
    if (stickRef.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, toolCalls, pendingApprovals, agentState]);

  const resize = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  return (
    <div className="flex h-full flex-col">
      <div
        className="min-h-0 flex-1 overflow-y-auto"
        ref={scrollerRef}
        onScroll={() => {
          const el = scrollerRef.current;
          if (!el) return;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {messages.length === 0 && toolCalls.length === 0 ? (
          <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6 text-center">
            <div className="mb-3 text-2xl font-semibold tracking-tight">
              下一步做什么？
            </div>
            <p className="max-w-md text-sm text-muted">
              {blocked
                ? "先在输入框选择供应商和模型，或打开「模型」添加。"
                : workMode === "plan"
                  ? "Plan 模式只读：先调研再给计划，不会改文件或执行命令。"
                  : "Auto 模式：给 agent 一条任务。文件写入和命令默认会先请你审批。"}
            </p>
            {!blocked && (
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {(workMode === "plan" ? EXAMPLES_PLAN : EXAMPLES_AUTO).map((ex) => (
                  <button
                    key={ex}
                    onClick={() => {
                      setInput(ex);
                      taRef.current?.focus();
                    }}
                    className="rounded-full border border-[var(--color-border)] bg-surface px-3 py-1.5 text-xs text-muted hover:border-[var(--color-border-strong)] hover:text-white"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className={`${COL} space-y-3 py-5`}>
            {(() => {
              const used = new Set<string>();
              const blocks: ReactNode[] = [];
              for (const m of messages) {
                const isUser = m.role === "user";
                const hasContent = Boolean(m.content && m.content.trim());
                const showBubble =
                  isUser ||
                  Boolean(m.streaming) ||
                  hasContent ||
                  Boolean(m.thinking) ||
                  Boolean(m.thinkingStreaming);
                const attached = toolCalls.filter((t) => t.name !== "finish_task" && t.messageId === m.id);
                attached.forEach((t) => used.add(t.call_id));
                if (showBubble) {
                  const isError = !isUser && m.content.startsWith("[错误]");
                  const caret = m.streaming && !m.thinkingStreaming && (
                    <span className="ml-0.5 animate-pulse text-accent">▍</span>
                  );
                  blocks.push(
                    <div key={m.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                      {isUser ? (
                        <div className="max-w-[min(75%,42rem)] rounded-2xl bg-accent-dim px-4 py-2.5 text-sm leading-6">
                          <p className="whitespace-pre-wrap">{m.content}</p>
                        </div>
                      ) : (
                        <div className="w-full min-w-0 text-sm leading-6">
                          <ThinkingBlock text={m.thinking} streaming={m.thinkingStreaming} />
                          {hasContent ? (
                            <p
                              className={`whitespace-pre-wrap ${
                                isError
                                  ? "rounded-xl border border-red-500/30 bg-red-950/30 px-3 py-2 text-red-200"
                                  : ""
                              }`}
                            >
                              {m.content}
                              {caret}
                            </p>
                          ) : (
                            caret
                          )}
                        </div>
                      )}
                    </div>,
                  );
                }
                for (const t of attached) {
                  blocks.push(<ToolCallCard key={t.call_id} tool={t} />);
                }
              }
              const orphan = toolCalls.filter((t) => t.name !== "finish_task" && !used.has(t.call_id));
              for (const t of orphan) {
                blocks.push(<ToolCallCard key={t.call_id} tool={t} />);
              }
              return blocks;
            })()}
            {agentState === "awaiting_approval" && pendingApprovals.length === 0 && (
              <p className="text-center text-xs text-amber-400">等待审批…</p>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="border-t border-[var(--color-border)] bg-surface/60 py-3 backdrop-blur">
        <div className={COL}>
          {pendingApprovals[0] && (
            <ApprovalModal key={pendingApprovals[0].approval_id} approval={pendingApprovals[0]} onRespond={respondApproval} />
          )}
          {pendingApprovals.length > 1 && (
            <p className="mb-2 text-[11px] text-amber-400/90">
              还有 {pendingApprovals.length - 1} 条命令排队等待审批。选「本次会话同类均执行」后，后续同类会自动放行。
            </p>
          )}
          {sendBlockedReason && <p className="mb-2 text-xs text-amber-400">{sendBlockedReason}</p>}
          {pickerErr && <p className="mb-2 text-xs text-red-300">{pickerErr}</p>}
          {workMode === "plan" && (
            <p className="mb-2 text-[11px] text-amber-400/90">Plan 模式：只读调研，不会改文件或执行命令</p>
          )}
          {models.length > 0 && draftModelId != null && draftModelId !== modelId && (
            <p className="mb-2 text-[11px] text-faint">模型将于下次发送后切换</p>
          )}
          <div className="rounded-2xl border border-[var(--color-border)] bg-surface-2 p-2 shadow-panel focus-within:border-accent">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                resize();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void onSend();
                }
              }}
              placeholder={
                blocked
                  ? "请先选择供应商和模型..."
                  : workMode === "plan"
                    ? "描述要规划的任务，Enter 发送"
                    : "输入任务，Enter 发送，Shift+Enter 换行"
              }
              disabled={blocked}
              className="max-h-40 min-h-[40px] w-full resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-faint disabled:opacity-50"
            />
            <div className="flex items-center gap-2 px-1 pb-0.5 pt-1">
              <div className="ui-seg shrink-0" role="group" aria-label="工作模式">
                <button
                  type="button"
                  className={workMode === "auto" ? "is-active" : ""}
                  title="Auto：可改文件与执行命令（需审批）"
                  onClick={() => {
                    if (workMode !== "auto") void setSessionWorkMode("auto").catch((e) => setPickerErr(String(e)));
                  }}
                >
                  Auto
                </button>
                <button
                  type="button"
                  className={workMode === "plan" ? "is-active is-plan" : ""}
                  title="Plan：只读调研，只输出计划"
                  onClick={() => {
                    if (workMode !== "plan") void setSessionWorkMode("plan").catch((e) => setPickerErr(String(e)));
                  }}
                >
                  Plan
                </button>
              </div>
              <select
                className="ui-select min-w-0 max-w-[42%] flex-1"
                aria-label="AI 供应商"
                title="供应商"
                value={draftProviderId ?? ""}
                onChange={(e) => onProviderChange(Number(e.target.value))}
              >
                {providerOpts.length === 0 && <option value="">演示模式</option>}
                {providerOpts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <select
                className="ui-select min-w-0 max-w-[42%] flex-1"
                aria-label="AI 模型"
                title="模型"
                value={draftModelId ?? ""}
                disabled={providerOpts.length === 0}
                onChange={(e) => setPickedModelId(e.target.value ? Number(e.target.value) : null)}
              >
                {modelsForProvider.length === 0 && <option value="">{models.length ? "选择模型…" : "演示模式"}</option>}
                {modelsForProvider.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.display_name}
                  </option>
                ))}
              </select>
              <button
                onClick={() => void onSend()}
                disabled={blocked || !input.trim()}
                className="ui-btn-primary ml-auto h-9 w-9 shrink-0 rounded-xl p-0"
                aria-label="发送"
              >
                <IconSend />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
