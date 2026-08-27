# Agent Harness

配套 `DESIGN.md` / `PLAN.md` — 纯个人单机多会话 coding agent。

## 启动 (Ubuntu VM 内)

1. MySQL/Redis 使用本机已有实例 (docker-compose 仅备选无本机时)
2. 后端: `cd backend && uv sync && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --reload`
3. 前端: `cd frontend && npm install && npm run dev -- --host`
4. 浏览器访问 `http://<VM_IP>:5173`

详见 `PLAN.md` §9。
