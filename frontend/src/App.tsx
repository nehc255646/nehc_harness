import { useEffect, useState } from "react";
import { useAgentStore } from "./store/agentStore";
import Chat from "./components/Chat";
import ModelSettings from "./components/ModelSettings";
import SessionSidebar from "./components/SessionSidebar";
import SubAgentPanel from "./components/subagent/SubAgentPanel";
import WorkerStatus from "./components/subagent/WorkerStatus";
import AccentPicker from "./components/AccentPicker";
import { IconMenu, IconPanelRight, IconSettings, IconStop } from "./components/icons";

const SIDEBAR_KEY = "harness.sessionSidebarCollapsed";

function readSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

const STATE_LABEL: Record<string, string> = {
  idle: "空闲",
  running: "运行中",
  awaiting_approval: "待审批",
  done: "完成",
  error: "出错",
};

const CONN_LABEL: Record<string, string> = {
  connected: "已连接",
  connecting: "连接中",
  disconnected: "已断开",
  error: "连接失败",
};

export default function App() {
  const {
    boot,
    connectionState,
    refreshModels,
    agentState,
    stopAgent,
    sessionTitle,
    sessionId,
    sessionRows,
    subPanels,
    workers,
    subPanelOpen,
    setSubPanelOpen,
    toggleSubAgent,
  } = useAgentStore();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const busy =
    agentState === "running" ||
    agentState === "awaiting_approval" ||
    workers.some((w) => w.state === "running") ||
    subPanels.some((p) => p.status === "running");
  const heading = sessionTitle || sessionRows.find((s) => s.id === sessionId)?.title || "Agent Harness";
  const subCount = subPanels.length;

  useEffect(() => {
    boot();
  }, [boot]);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarCollapsed]);

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-[var(--color-text)]">
      <SessionSidebar
        mobileOpen={sidebarOpen}
        collapsed={sidebarCollapsed}
        onClose={() => setSidebarOpen(false)}
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] bg-surface/80 px-3 backdrop-blur sm:px-4">
          <div className="flex min-w-0 items-center gap-2">
            <button
              className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-white md:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="打开会话列表"
            >
              <IconMenu />
            </button>
            <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold tracking-tight">{heading}</h1>
            <p className="flex items-center gap-1.5 text-[11px] text-faint">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  connectionState === "connected"
                    ? "bg-accent shadow-[0_0_6px_var(--color-accent-glow)]"
                    : connectionState === "connecting"
                      ? "bg-amber-400"
                      : "bg-red-500"
                }`}
              />
              {STATE_LABEL[agentState] || agentState}
              <span className="text-[var(--color-border-strong)]">·</span>
              {CONN_LABEL[connectionState] || connectionState}
            </p>
            </div>
          </div>
          <div className="flex shrink-0 flex-nowrap items-center gap-2">
            <div className="hidden sm:block">
              <AccentPicker />
            </div>
            <button
              type="button"
              onClick={() => toggleSubAgent()}
              className={`ui-btn-ghost shrink-0 whitespace-nowrap px-2 ${subPanelOpen ? "text-accent" : ""}`}
              title={subPanelOpen ? "收起子 Agent 侧栏" : "打开子 Agent 侧栏"}
              aria-pressed={subPanelOpen}
            >
              <IconPanelRight className="h-4 w-4" />
              <span className="hidden sm:inline">侧栏</span>
              {(subCount > 0 || workers.length > 0) && (
                <span className="rounded-full bg-accent-dim px-1.5 text-[10px] text-accent">
                  {subCount + workers.length}
                </span>
              )}
            </button>
            <button
              onClick={() => setSettingsOpen(true)}
              className="ui-btn-ghost shrink-0 whitespace-nowrap px-2"
              title="模型与供应商"
            >
              <IconSettings className="h-4 w-4" />
              <span className="hidden lg:inline">模型</span>
            </button>
            {busy && (
              <button onClick={() => stopAgent()} className="ui-btn shrink-0 gap-1 whitespace-nowrap border border-red-500/30 bg-red-950/40 px-2.5 text-xs text-red-300 hover:bg-red-950/70">
                <IconStop />
                停止
              </button>
            )}
          </div>
        </header>
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            {!subPanelOpen && workers.length > 0 && <WorkerStatus peek />}
            <div className="min-h-0 flex-1 overflow-hidden">
              <Chat />
            </div>
          </div>
          <SubAgentPanel open={subPanelOpen} onClose={() => setSubPanelOpen(false)} />
        </div>
      </div>
      <ModelSettings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onChanged={() => {
          refreshModels();
        }}
      />
    </div>
  );
}
