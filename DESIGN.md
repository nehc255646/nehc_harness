# Agent Harness — 设计方案

一个类似 Codex / Claude Code 的 Web 终端 coding agent。主 agent 持续工作，可按需生成两类**单次使用的子 agent**：与用户临时交流的**交互型**（侧栏）与后台并发工作的**工作型**（与主 agent 同等权限），结果均异步回投主 agent。以 LangChain 为核心，FastAPI 后端 + React 前端 + MySQL 持久层 + Redis 实时层。

---

## 1. 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.14 + FastAPI (async) + LangChain |
| 工具定义 | LangChain `@tool` 装饰器 / `BaseTool` |
| 编排层 | 手写 asyncio loop（混合：工具用 LangChain，编排自己写） |
| ORM | SQLAlchemy 2.0 async + Alembic 迁移 + MySQL 8 |
| 缓存/实时 | Redis（agent 状态、pending 审批与会话放行规则、断线恢复；pub/sub 事件路由不启用） |
| 前端 | React 18 + TypeScript + Vite + TailwindCSS + zustand（SPA）；主题：暗色（黑色）打底，强调色/特效以青色为主，后期支持主题色切换 |
| 通信 | WebSocket（流式事件 + 定向审批/问答） |
| 部署 | Ubuntu VM（VMware）内全栈运行：MySQL/Redis docker-compose，前后端本地运行 |

### 环境（已确认）
- 运行环境：**Ubuntu 虚拟机（VMware）**，代码仓放 VM ext4；Windows 侧经 VS Code Remote-SSH 开发、浏览器访问 VM IP；演示/危险测试前打快照
- MySQL 8 / Redis 使用 VM 本机已有实例（localhost:3306 / localhost:6379）；docker-compose 仅在无本机实例时启用
- 模型走 OpenAI 兼容接口（按供应商分组存 DB：Provider{provider_id/base_url/api_key} → 多 Model{model_id/display_name}，仅 api_key 认证，全部手动创建，提供 `hello` 探测按钮（可选测试，失败仍可保存）；新建默认按“上一次使用 > 用户自定义兜底 > 留空”解析）

---

## 2. 核心设计

### 2.1 编排层：手写 asyncio loop（混合）

LangChain 只用于**定义工具**（`@tool`）和**模型封装**（`ChatOpenAI`）；agent 编排自己手写，以获得对 per-agent 暂停/审批、并发子 agent 的完全控制。

### 2.2 多 Agent 并发模型

**每个 agent 一条异步消息队列**（asyncio.Queue，Redis 备份）：

```
主 agent loop（每轮迭代）:
  1. 从自己的队列取事件（含"子 agent 结果"这类异步到达的消息）
  2. 若有 → 作为上下文注入本轮，再决定下一步
  3. 否则继续当前工作
  4. 需要临时交流时 → 调用 spawn_subagent()，发出即走，不阻塞
```

- 主 agent 全程持续工作，不因子 agent 暂停
- 每条消息/审批带 `agent_id` 寻址
- 确认门是 **per-agent** 的：一个 agent 卡在审批，其他照常跑

### 2.3 子 Agent：单次使用 + 隔离 + 异步回投（分两类）

**交互型**（与用户临时交流）：
```
主 agent → spawn_subagent(context)
  → 快照：历史 + 行为描述 + 历史子 agent 记录
  → 隔离上下文 + 独立 task，前端侧栏开面板
  → 只与用户对话（无工具，仅 finish_subagent），不走用户门
  → finish_subagent → 异步投回主 agent 队列 → 销毁
```

**工作型**（主 agent 主动派生的后台工作者）：
```
主 agent → spawn_worker(task, constraints)
  → 同交互型快照逻辑生成隔离上下文 + 独立 task（后台运行，默认不弹侧栏，按 batch_id 分组）
  → 与主 agent 同等命令权限：全量工具 + 同套用户门（会话级共享 allow_rules），并发执行
  → 前端仅在“工作区”显示工作列表（id/任务摘要/状态，无详情），WS 推送 `worker.status`
  → 自主工作至 finish_worker(result) 或 `MAX_ROUNDS`/`WORKER_TIMEOUT`(600s) 触顶 → 暂存
  → 同 `batch_id` 全部结束后，在主 agent 此后第一个节点边界以单条 `worker.batch_done` 聚合事件批量注入
  → 约束：防全量转包（单轮 ≤2，总并发 ≤3，独立 `WORKER_SYSTEM_PROMPT`），暂不支持递归；error/超时按完成随批量回投
```

- **隔离**：上下文是冻结快照（不含主 agent 之后的新动作），不共享可变状态；两类子 agent 共享并发池 `SUBAGENT_MAX_CONCURRENCY=3`
- **单次使用**：生命周期只有一次，返回后即销毁
- **时序兜底**：若主 agent 已完成 → 结果仍存入会话记录标"迟到结果"，供续聊/回顾使用

### 2.4 用户门（per-agent，安全由用户承担）

- 所有非放行的工具调用**首次执行均弹审批**，前端三选：[执行一次] [本次会话同类命令均执行] [拒绝]
- 「同类」固定取命令前 2 个 token 前缀匹配（如批准 `git push` 放行 `git push *`）；工具类按工具名；规则会话级共享（主+子 agent）、Redis 存储
- 加速层：配置文件持久放行规则（`allow_rules.yaml`）+ 只读工具默认放行（可配置改为需审批）
- 兜底层：极简黑名单（Unix 破坏性命令，默认开、可配置关）直接拒绝
- 挂起用 `asyncio.Future`，**必须带超时 + WS 断连兜底**（默认拒绝并通知前端），避免前端刷新导致 agent 永久卡死

### 2.5 前端布局

- **主聊天区**：流式渲染、工具调用卡、文件 diff
- **侧栏**：交互型子 agent 会话面板，按需弹出、可折叠，完成后标"已返回结果"
- **工作区**：后台工作型列表区域（显示 workers[]{id/任务摘要/状态}，青色状态指示，无详细信息，默认在后台）
- WebSocket 双向：事件流 + 定向审批/问答（新增 `worker.status` 列表推送）

### 2.6 主题与视觉

- **基色**：暗色（黑色）打底，`#0A0A0A` / `#000` 为背景主色
- **强调色**：青色（`cyan-400/500`，如 `#22D3EE` / `#06B6D4`）用于按钮、链接、选中态、流式光标、WS 连接指示、审批高亮等特效
- **实现**：Tailwind `dark` 模式 + CSS 变量（`--color-accent` / `--color-bg`），后期抽 `ThemeProvider` + zustand `theme` store 支持主题色切换（预留，不阻塞 M1）

---

## 3. 数据分层

| 存储 | 职责 |
|---|---|
| **MySQL**（持久） | Session、Message、ToolLog、SubAgentRun、Provider、Model（按供应商分组）、AppConfig（兜底模型） |
| **Redis**（实时） | 进行中 agent 状态注册表、pending 审批队列与会话放行规则（TTL）、断线重连恢复 |

---

## 4. 目录结构（目标）

```
Harness/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                    # 数据库迁移
│   └── app/
│       ├── main.py                 # FastAPI 入口，挂载 WS 路由
│       ├── api/
│       │   ├── ws.py               # WebSocket 端点（会话流 + 定向交互）
│       │   └── rest.py             # REST：会话 CRUD、Provider/Model 配置（分组）、历史
│       ├── agent/
│       │   ├── loop.py             # 手写 asyncio loop（含工作型完整 loop，走同套 policy/审批）
│       │   ├── subagent.py         # 单次子 agent（快照/隔离/回投，分交互型/工作型，暂不支持递归）
│       │   ├── manager.py          # AgentManager 注册表
│       │   ├── prompts.py
│       │   └── executor.py         # LangChain 模型调用封装
│       ├── tools/
│       │   ├── files.py            # read/write/edit/glob/grep
│       │   ├── shell.py            # 命令执行（沙箱）
│       │   ├── subagent_tool.py    # spawn_subagent（交互型）
│       │   ├── worker_tool.py      # spawn_worker / finish_worker（工作型，同等权限，后台并发，防全量转包约束）
│       │   └── registry.py
│       ├── permissions/
│       │   ├── policy.py           # 用户门分流：黑名单/配置规则/会话规则/审批
│       │   ├── rules.py            # 规则解析与前缀匹配
│       │   └── gate.py             # per-agent 审批挂起/恢复
│       ├── core/
│       │   ├── config.py           # pydantic-settings 读 .env
│       │   └── redis.py            # Redis 客户端 + 状态/队列
│       ├── models/                 # SQLAlchemy ORM
│       └── schemas/                # Pydantic 请求/响应
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── api/ws.ts               # WebSocket 客户端封装
│       ├── components/
│       │   ├── Chat.tsx            # 对话流
│       │   ├── ToolCallCard.tsx    # 工具调用展示
│       │   ├── ApprovalModal.tsx   # 危险命令确认框
│       │   ├── CodeDiff.tsx        # 文件 diff
│       │   └── SessionSidebar.tsx  # 会话列表/切换
│       ├── components/subagent/
│       │   ├── SubAgentPanel.tsx   # 侧栏交互型面板
│       │   └── WorkerStatus.tsx    # 后台工作型列表（无详情，青色指示）
│       └── hooks/useAgentStream.ts # 处理流式事件（含 worker.status）
├── docker-compose.yml              # 预留（本机已有则不用）
└── .env.example
```

---

## 5. 里程碑

| 里程碑 | 内容 |
|---|---|
| **M1 — 核心闭环** | 脚手架 → 模型封装 + 手写 loop → 3 个文件工具 → shell + 用户门 → WS 打通 → 极简聊天 UI |
| **M2 — 子 agent** | 交互型 `spawn_subagent` + 工作型 `spawn_worker`（同等权限、后台并发、防全量转包、暂无递归）+ 快照/隔离/异步回投 |
| **M3 — 持久化** | SQLAlchemy 模型 + Alembic + MySQL + 会话续聊 |
| **M4 — Redis 全面（WS 断线恢复）** | agent 状态、pending TTL、会话放行规则、断线恢复 |
| **M5 — 打磨** | diff、多会话侧栏、美化、测试、文档 |

---

## 6. 关键决策记录

- **手写 loop 而非 LangGraph / AgentExecutor**：需要 per-agent 独立暂停 + 并发子 agent + 定向交互，AgentExecutor 封装过死，LangGraph interrupt 是整图级的，手写最可控且贴合需求。
- **Redis 定位**：多 agent 寻址、pending 队列、断线恢复是刚需；pub/sub 事件路由留待横向扩展时再启用。
- **子 agent 为单次使用、隔离快照**：分交互型（与用户对话，无工具）与工作型（后台并发，与主 agent 同等权限，防全量转包，暂无递归），不是常驻并发体，降低状态一致性复杂度。
- **审批门必须带超时 + 断连兜底**：防止 WS 断连导致 agent 永久挂起。
- **安全模型**：安全由用户承担——所有非放行命令首次执行弹审批（三选），支持会话级前缀放行；极简黑名单仅作防误伤安全网，可配置关闭。
- **单进程硬约束**：AgentManager 为进程内注册表，后端必须 `--workers 1`；进程崩溃丢在途状态为已知限制，恢复边界 = DB 已落库历史。
- **CodeDiff 在 M5**：diff 结构化数据依赖 M3 落库后的 write/edit ToolLog，M1–M2 无稳定数据源。
