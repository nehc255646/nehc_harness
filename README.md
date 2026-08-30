# Agent Harness

[English](README.en.md) · 中文

个人单机、单用户的 Web coding agent：主对话流式工作，顶栏开交互型子 agent，主 agent 可派生后台工作型。文件写入和 shell 默认先审批。

无鉴权、无限流。默认只监听本机。不要暴露到公网。

---

## 能做什么

| | |
|---|---|
| 对话 | 流式正文与思考通道、工具卡、write/edit 行级 diff |
| 模式 | 底栏 **Auto**（可改文件、跑命令）/ **Plan**（只读调研并给计划） |
| 审批 | 黑名单拒绝 · `allow_rules.yaml` 放行 · 会话「同类均执行」· 首次三选 |
| 子 agent | 交互型（侧栏对话，结束摘要回投）· 工作型（后台并发，按批回投） |
| 会话 | 列表、续聊、重命名；历史在 MySQL，重启仍在 |
| 模型 | OpenAI 兼容供应商分组；`api_key` Fernet 加密；可从 `*_API_KEY` 环境变量读 |

同类（会话放行）：shell 看命令前 2 个 token；其它工具按工具名。链式命令只要开头同类即放行。

## 技术栈

后端 Python 3.14 · FastAPI · 手写 asyncio loop · LangChain。权威存储 MySQL 8（Alembic）。Redis 镜像 agent 状态、进行中审批、会话放行规则、摘要缓存（连不上则降级内存）。前端 React 18 · TypeScript · Vite · Tailwind · zustand · WebSocket。

后端必须 **单进程**（`uvicorn --workers 1`）。`AgentManager` 是进程内注册表，多 worker 会打乱审批与寻址。

## 环境

- Python 3.14 + [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- MySQL 8（`localhost:3306`；没有就用仓库根目录 `docker-compose.yml`）
- Redis（断线恢复 / 会话放行规则镜像需要它）

## 快速开始

```bash
cp .env.example .env
```

至少填：

| 变量 | 说明 |
|---|---|
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | 建议库名、用户都用 `harness`，不要用 root 跑日常 |
| `ENCRYPTION_KEY` | Fernet 密钥，加密供应商 api_key。已有供应商时缺密钥会拒绝启动 |

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

一次性建库：

```sql
CREATE DATABASE harness CHARACTER SET utf8mb4;
CREATE USER 'harness'@'localhost' IDENTIFIED BY '...';
GRANT ALL ON harness.* TO 'harness'@'localhost';
FLUSH PRIVILEGES;
```

没有本机 MySQL / Redis：

```bash
docker compose up -d
```

Compose 把 3306 / 6379 绑在 `127.0.0.1`。然后：

```bash
chmod +x start.sh
./start.sh
```

会拉起后端 `:8000`（单 worker）和前端 `:5173`，并尝试打开浏览器。两者默认 `127.0.0.1`。

虚拟机要从宿主机访问：

```bash
HARNESS_BIND=0.0.0.0 ./start.sh
```

无鉴权，只在可信网络用。同时把宿主机源加进 `CORS_ORIGINS`。

手动启动：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# 另开终端
cd frontend
npm install
npm run dev
```

Vite 把 `/api`、`/ws` 代理到 `:8000`。打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。

## 使用

1. 打开「模型」，加 OpenAI 兼容的 `base_url` + `api_key`（或勾选 `*_API_KEY` 环境变量），再按模型测连接（失败仍可保存）。
2. 输入框底栏切 Auto / Plan，先选供应商再选模型。模型切换在下一次发送后生效。未配置任何模型时走 heuristic 演示：发「执行 echo hello」可看到审批。
3. 非放行的 `shell` / `write` / `edit` 弹三选：**执行一次** / **本次会话同类均执行** / **拒绝**。
4. 顶栏「子 Agent」打开交互型侧栏（关侧栏 ≠ 终止；侧栏内可停止）。主 agent 可派生工作型，列表在底部工作区。顶栏「停止」停主 agent 及其子 agent。
5. 文件与 shell 锁在 `WORKDIR`（默认仓库下 `workspace/`）。持久放行见 `allow_rules.yaml`。

## 安全模型

这不是沙箱。审批和黑名单是给人用的门，拦不住有意绕过。

- 黑名单按 `;` `&&` `||` `|` 拆段，外加 `rm -rf` 一类破坏性写法；不是命令语义分析。
- 配置放行要求**每一段**都要命中前缀。会话「同类」按**整条命令开头**匹配（这是刻意的，否则同类放行没用）。
- `read` / `write` / `edit` 约束在 `WORKDIR` 内，不跟随指向外部的 symlink；`grep` 同样不跟出去。
- shell 子进程不继承 `ENCRYPTION_KEY`、`MYSQL_*`、`*_API_KEY` 等密钥环境变量。
- `/api/llm/probe` 只允许 `http(s)`，环境变量名须以 `_API_KEY` 结尾。

## 配置（节选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WORKDIR` | `./workspace` | 文件与 shell 根目录 |
| `HOST` | `127.0.0.1` | 后端绑定；对外需 `0.0.0.0` 并设 `CORS_ORIGINS` |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 浏览器 Origin 白名单 |
| `LLM_TIMEOUT` | `180` | 模型请求超时（秒） |
| `MAX_ROUNDS` | `50` | 单 run 最大轮数 |
| `WINDOW_N` | `20` | 滑动窗口 turn 数 |
| `SUBAGENT_MAX_CONCURRENCY` | `3` | 子 agent 总并发 |
| `MAX_WORKERS_PER_TURN` | `2` | 单轮工作型上限 |
| `APPROVAL_TIMEOUT` | `120` | 审批超时默认拒绝（秒） |
| `WORKER_TIMEOUT` | `600` | 工作型整体超时（秒） |
| `BLACKLIST_ENABLED` | `true` | 破坏性命令黑名单 |
| `READONLY_NEED_APPROVAL` | `false` | 只读工具是否也要审批 |
| `REDIS_URL` | `redis://localhost:6379/0` | 实时层 |

完整列表见 `.env.example`。

## 开发

```bash
cd backend && uv run ruff check app tests && uv run pytest
cd frontend && npx tsc --noEmit && npm run lint
```

REST：`http://127.0.0.1:8000/docs`。健康检查：`GET /health`、`GET /api/health`。

集成测试会往 MySQL 写 `it_` / `ut_` 会话和 `example.invalid` 供应商，测完可自行删掉。

## 已知限制

- 进程崩溃丢掉在途 loop / 审批 Future；恢复边界是 **MySQL 已落库的历史**
- 多会话共用同一 `workspace`，可能同时改同一文件
- Redis TTL 过期不代表 agent 结束；无 Redis 时断线恢复只靠内存
- 交互型侧栏对话主要在内存；整页刷新后侧栏记录可能空，摘要仍会回投

## 许可证

个人项目，未指定开源许可证。默认保留所有权利。
