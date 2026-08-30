#!/usr/bin/env bash
# 启动 Agent Harness（后端 :8000 单进程 + 前端 :5173）并打开网页
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

BACKEND_PORT=8000
FRONTEND_PORT=5173
BIND="${HARNESS_BIND:-127.0.0.1}"

log()  { echo -e "\033[36m[harness]\033[0m $*"; }
warn() { echo -e "\033[33m[harness]\033[0m $*" >&2; }

# 占用目标端口的旧进程先清理（开发脚本，幂等重启）
kill_port() {
  local port="$1"
  local pids
  pids=$(fuser -n tcp "$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    warn "端口 $port 被占用 (pid: $pids)，先停止旧进程"
    fuser -k -n tcp "$port" >/dev/null 2>&1 || true
    sleep 1
  fi
}

kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"

# 环境文件检查
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  warn "已从 .env.example 生成 .env，请填写 MYSQL_PASSWORD / ENCRYPTION_KEY 后重跑"
  exit 1
fi

# Ctrl+C / 退出时回收子进程
cleanup() {
  log "正在停止服务..."
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ---------- 后端 ----------
log "启动后端 (uvicorn --workers 1)..."
(
  cd "$ROOT/backend"
  [ -d .venv ] || uv sync
  uv run alembic upgrade head
  exec uv run uvicorn app.main:app --host "$BIND" --port "$BACKEND_PORT" --workers 1
) >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

# ---------- 前端 ----------
if [ "$BIND" = "0.0.0.0" ]; then
  warn "绑定 0.0.0.0：无鉴权控制面暴露到局域网，仅在可信网络使用"
fi
log "启动前端 (vite --host $BIND)..."
(
  cd "$ROOT/frontend"
  [ -d node_modules ] || npm install
  exec npm run dev -- --host "$BIND" --port "$FRONTEND_PORT" --strictPort
) >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

# ---------- 等待就绪 ----------
wait_url() {
  local url="$1" name="$2" i
  for i in $(seq 1 60); do
    if curl -sf -o /dev/null "$url"; then
      log "$name 就绪: $url"
      return 0
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null && [ "$name" = "后端" ]; then
      warn "$name 启动失败，日志: $LOG_DIR/backend.log"
      tail -20 "$LOG_DIR/backend.log" >&2 || true
      exit 1
    fi
    sleep 1
  done
  warn "$name 等待超时（60s），日志: $LOG_DIR/"
  return 1
}

wait_url "http://localhost:$BACKEND_PORT/health" "后端" || true
wait_url "http://localhost:$FRONTEND_PORT" "前端" || true

# ---------- 打开网页 ----------
URL="http://localhost:$FRONTEND_PORT"
# VM 内无桌面时，给出 Windows 侧可访问的地址
VM_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  xdg-open "$URL" >/dev/null 2>&1 && log "已在浏览器打开 $URL" || warn "自动打开失败，请手动访问 $URL"
else
  warn "VM 内无图形环境，请在浏览器访问:"
  log "  http://127.0.0.1:$FRONTEND_PORT  (本机)"
  if [ "$BIND" = "0.0.0.0" ] && [ -n "$VM_IP" ]; then
    log "  http://$VM_IP:$FRONTEND_PORT    (局域网，无鉴权)"
  else
    log "  局域网访问: HARNESS_BIND=0.0.0.0 ./start.sh"
  fi
fi

log "服务运行中，Ctrl+C 停止全部。日志: $LOG_DIR/backend.log / $LOG_DIR/frontend.log"
wait
