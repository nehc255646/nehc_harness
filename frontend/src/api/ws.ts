/** WebSocket 客户端封装 */

export type WSEvent = {
  event: string;
  payload: Record<string, unknown>;
};

export class HarnessWS {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Map<string, Set<(payload: Record<string, unknown>) => void>> = new Map();
  private reconnectTimer: number | null = null;
  // 跟踪最新会话，断线重连时使用（session.select 切换后不回连旧会话）
  private sessionId = "default";

  constructor(url?: string) {
    // .env VITE_WS_URL 或默认同源（Vite dev 已代理 /ws → 后端）；base 可带可不带 /ws，统一归一
    const envUrl = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_WS_URL;
    const base = (url || envUrl || "").replace(/\/+$/, "").replace(/\/ws$/, "");
    this.url = base
      ? `${base}/ws`
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  }

  connect(sessionId = "default") {
    // 关闭旧连接（detach 回调避免触发重连定时器），支持幂等调用
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      try {
        this.ws.close();
      } catch {
        /* ignore */
      }
      this.ws = null;
    }
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.sessionId = sessionId;
    const full = `${this.url}?session_id=${sessionId}`;
    this.ws = new WebSocket(full);
    this.ws.onopen = () => this.emit("connection", { state: "connected" });
    this.ws.onclose = () => {
      this.emit("connection", { state: "disconnected" });
      // 简单重连，使用最新会话
      if (this.reconnectTimer === null) {
        this.reconnectTimer = window.setTimeout(() => {
          this.reconnectTimer = null;
          this.connect(this.sessionId);
        }, 2000);
      }
    };
    this.ws.onerror = () => this.emit("connection", { state: "error" });
    this.ws.onmessage = (e) => {
      try {
        const msg: WSEvent = JSON.parse(e.data);
        if (msg.event === "ping") this.send("pong");
        this.emit(msg.event, msg.payload);
        this.emit("*", msg as unknown as Record<string, unknown>);
      } catch {
        console.warn("WS invalid json:", e.data);
      }
    };
  }

  setSession(sessionId: string) {
    this.sessionId = sessionId;
  }

  send(event: string, payload: Record<string, unknown> = {}) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ event, payload }));
    }
  }

  on(event: string, cb: (payload: Record<string, unknown>) => void) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event)!.add(cb);
    return () => this.handlers.get(event)?.delete(cb);
  }

  private emit(event: string, payload: Record<string, unknown>) {
    this.handlers.get(event)?.forEach((cb) => cb(payload));
    if (event !== "*") this.handlers.get("*")?.forEach((cb) => cb({ event, payload } as unknown as Record<string, unknown>));
  }

  close() {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}

export const wsClient = new HarnessWS();
