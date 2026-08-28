# Agent Harness

个人单机、多会话的 Web coding agent。主 agent 可持续工作，按需派生交互型（侧栏对话）或工作型（后台并发）子 agent，工具执行走用户审批。

配套设计见 `DESIGN.md` / `PLAN.md`。

## 技术栈

- 后端：Python 3.14 · FastAPI · 手写 asyncio loop · LangChain（工具 / ChatOpenAI）
- 存储：MySQL 8（权威）· Redis（实时态，全面能力仍在后续里程碑）
- 前端：React 18 · Vite · Tailwind · zustand · WebSocket

## 环境

- Python 3.14 + [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- MySQL 8（本机已有即可；无实例时可用仓库根目录 `docker-compose.yml`）
- Redis（连不上仅告警，可先不配）

```bash
cp .env.example .env
```

至少填写：

| 变量 | 说明 |
|---|---|
| `MYSQL_*` | 库 `harness`、用户 `harness`（不要用 root 跑日常） |
| `ENCRYPTION_KEY` | Fernet 密钥，用于加密 Provider api_key |

生成密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

模型在 UI「模型」里按供应商分组手动添加（OpenAI 兼容：base_url + api_key）。未配置模型时走 heuristic 演示。

## 启动

后端必须单进程（`--workers 1`），AgentManager 是进程内注册表。

```bash
# 1. 建库（一次性）
# CREATE DATABASE harness;
# CREATE USER 'harness'@'localhost' IDENTIFIED BY '...';
# GRANT ALL ON harness.* TO 'harness'@'localhost';

# 2. 后端
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --reload

# 3. 前端
cd frontend
npm install
npm run dev -- --host
```

浏览器打开 `http://localhost:5173`（远程 VM 则换成该机 IP）。Vite 已把 `/api`、`/ws` 代理到 `:8000`。

无本机 MySQL/Redis 时：

```bash
docker compose up -d
```

## 当前进度

- **M1** 核心闭环：手写 loop、文件/shell 工具、用户门、WS 聊天
- **M2** 子 agent：交互型 + 工作型（快照隔离、异步回投）
- **M3** 持久化：会话续聊、Provider/Model、落库与重启 hydrate
- **M4** Redis 全面 / **M5** 打磨：未做

已知限制：进程崩溃会丢掉在途 loop / 审批 Future；恢复边界是已落库历史。安全由用户承担——首次非放行命令弹审批，黑名单只是兜底。放行规则见 `allow_rules.yaml`。Agent 工作目录约束在 `WORKDIR`（默认 `./workspace`）。

## 开发

```bash
cd backend && uv run ruff check app tests && uv run pytest
cd frontend && npx tsc --noEmit && npm run lint
```
