/** WebSocket 客户端封装 */

export type WSEvent = {
  event: string;
  payload: Record<string, unknown>;
};

type Outbound = { event: string; payload: Record<string, unknown> };

export class NeharnessWS {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Map<string, Set<(payload: Record<string, unknown>) => void>> = new Map();
  private reconnectTimer: number | null = null;
  private sessionId = "default";
  private manualClose = false;
  private outbound: Outbound[] = [];
  private attempt = 0;

  constructor(url?: string) {
    const envUrl = (import.meta as unknown as { env: Record<string, string> }).env?.VITE_WS_URL;
    const base = (url || envUrl || "").replace(/\/+$/, "").replace(/\/ws$/, "");
    this.url = base
      ? `${base}/ws`
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  }

  private teardownSocket() {
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
  }

  connect(sessionId = "default") {
    this.manualClose = false;
    this.teardownSocket();
    this.sessionId = sessionId;
    this.emit("connection", { state: "connecting" });
    const full = `${this.url}?session_id=${encodeURIComponent(sessionId)}`;
    this.ws = new WebSocket(full);
    this.ws.onopen = () => {
      this.attempt = 0;
      this.emit("connection", { state: "connected" });
      const queued = this.outbound.splice(0);
      for (const item of queued) {
        try {
          this.ws?.send(JSON.stringify(item));
        } catch {
          this.outbound.unshift(item);
          break;
        }
      }
    };
    this.ws.onclose = () => {
      this.emit("connection", { state: "disconnected" });
      if (!this.manualClose) this.scheduleReconnect();
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

  private scheduleReconnect() {
    if (this.reconnectTimer !== null || this.manualClose) return;
    const delay = Math.min(15000, 1000 * 2 ** Math.min(this.attempt, 4));
    this.attempt += 1;
    this.emit("connection", { state: "connecting" });
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.manualClose) this.connect(this.sessionId);
    }, delay);
  }

  setSession(sessionId: string) {
    this.sessionId = sessionId;
  }

  send(event: string, payload: Record<string, unknown> = {}) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ event, payload }));
      return true;
    }
    if (this.manualClose) return false;
    if (this.outbound.length >= 50) this.outbound.shift();
    this.outbound.push({ event, payload });
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connect(this.sessionId);
    }
    return false;
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
    this.manualClose = true;
    this.outbound = [];
    this.teardownSocket();
  }
}

export const wsClient = new NeharnessWS();
