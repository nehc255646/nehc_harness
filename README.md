# Agent Harness

[English](README.en.md) · 中文

个人单机、单用户、多会话的 Web 终端 coding agent。主 agent 可持续工作，按需派生**交互型**（侧栏对话）或**工作型**（后台并发）子 agent；文件与命令默认走用户审批。

> 安全由使用者承担：非放行命令首次执行会弹三选审批。黑名单只是兜底，不是沙箱。

---

## 功能

- 主聊天流式输出、工具调用卡、write/edit 行级 diff
- 工作模式 Auto（可执行）/ Plan（只读计划），底栏切换；Plan 使用独立 system prompt 与只读工具集
- 用户门：黑名单拒绝 / 配置放行 / 会话「同类均执行」/ 首次审批
- 交互型子 agent：侧栏对话，结束后摘要回投主 agent
- 工作型子 agent：后台并发、同等工具权限，按批次聚合回投（单轮最多 2 个，总并发 3）
- 会话列表、续聊、重命名；重启后端后历史仍在
- 供应商 / 模型分组配置（OpenAI 兼容接口），api_key 加密存储
- WebSocket 断线重连：进行中审批与会话放行规则可恢复（需 Redis）
- 长命令 `tool.progress` 尾部节流；顶栏强调色（青 / 绿 / 紫 / 琥珀）

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.14 · FastAPI · 手写 asyncio loop · LangChain（工具定义 / `ChatOpenAI`） |
| 持久化 | MySQL 8（权威）· Alembic |
| 实时 | Redis（agent 状态、pending 审批、会话放行规则、摘要缓存） |
| 前端 | React 18 · TypeScript · Vite · Tailwind · zustand · WebSocket |

硬约束：后端必须 **单进程**（`uvicorn --workers 1`）。`AgentManager` 是进程内注册表，多 worker 会打乱审批与寻址。

## 环境

- Python 3.14 + [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- MySQL 8（本机 `localhost:3306` 即可；没有实例时用仓库根目录 `docker-compose.yml`）
- Redis（连不上仅告警并降级内存；断线恢复 / 会话放行规则镜像需要 Redis）

## 快速开始

```bash
cp .env.example .env
```

至少填写：

| 变量 | 说明 |
|---|---|
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | 建议库名 `harness`、用户 `harness`，不要用 root 跑日常 |
| `ENCRYPTION_KEY` | Fernet 密钥，用于加密 Provider 的 api_key |

生成密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

一次性建库（示例）：

```sql
CREATE DATABASE harness CHARACTER SET utf8mb4;
CREATE USER 'harness'@'localhost' IDENTIFIED BY '...';
GRANT ALL ON harness.* TO 'harness'@'localhost';
FLUSH PRIVILEGES;
```

无本机 MySQL / Redis 时：

```bash
docker compose up -d
```

启动（推荐）：

```bash
chmod +x start.sh
./start.sh
```

脚本会拉起后端 `:8000`（单 worker）和前端 `:5173`，并尝试打开浏览器。虚拟机无桌面时，在宿主机浏览器访问 `http://<VM_IP>:5173`（需放行 5173 / 8000）。

手动启动：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# 另开终端
cd frontend
npm install
npm run dev -- --host
```

Vite 已把 `/api`、`/ws` 代理到 `:8000`。浏览器打开 `http://localhost:5173`。

## 使用

1. 打开「模型」，按供应商添加 OpenAI 兼容的 `base_url` + `api_key`，再为每条模型单独测试连接（失败仍可保存）。
2. 在对话输入框底部切换 Auto / Plan，并先选供应商再选模型。Auto 为当前可执行模式；Plan 只读调研并输出计划（不改文件、不跑命令、不派生子 agent）。模型切换在下一次发送后生效。未配置任何模型时走 heuristic 演示（可发「执行 echo hello」看审批）。
3. 非放行的 `shell` / `write` / `edit` 会弹审批：**执行一次** / **本次会话同类均执行** / **拒绝**。同类：shell 取命令前 2 个 token；其它工具按工具名。
4. 主 agent 可派生交互型（侧栏）或工作型（底部工作区）。关侧栏 ≠ 终止子 agent。
5. 工作目录锁定在 `WORKDIR`（默认仓库下 `workspace/`）。放行规则见 `allow_rules.yaml`。

## 配置（节选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WORKDIR` | `./workspace` | 文件与 shell 的根目录 |
| `MAX_ROUNDS` | 50 | 单 run 最大轮数 |
| `WINDOW_N` | 20 | 滑动窗口 turn 数 |
| `SUBAGENT_MAX_CONCURRENCY` | 3 | 子 agent 总并发 |
| `MAX_WORKERS_PER_TURN` | 2 | 单轮工作型上限 |
| `APPROVAL_TIMEOUT` | 120s | 审批超时默认拒绝 |
| `WORKER_TIMEOUT` | 600s | 工作型整体超时 |
| `BLACKLIST_ENABLED` | true | 极简破坏性命令黑名单 |
| `READONLY_NEED_APPROVAL` | false | 只读工具是否也要审批 |
| `REDIS_URL` | `redis://localhost:6379/0` | 实时层 |

完整列表见 `.env.example`。

## 开发

```bash
cd backend && uv run ruff check app tests && uv run pytest
cd frontend && npx tsc --noEmit && npm run lint
./scripts/demo.sh
```

REST 文档：后端起来后访问 `http://localhost:8000/docs`。健康检查：`GET /health`、`GET /api/status`。

## 已知限制

- 进程崩溃会丢掉在途 loop / 审批 Future；恢复边界是 **MySQL 已落库历史**
- 多会话共用同一 `workspace`，可能同时改同一文件
- 单用户、无鉴权、无限流；不适合暴露到公网
- 黑名单按 `;` / `&&` / 管道拆分匹配，不是命令语义级安全分析
- Redis TTL 过期不代表 agent 结束；无 Redis 时断线恢复降级为纯内存

## 许可证

个人项目，未指定开源许可证。默认保留所有权利。
