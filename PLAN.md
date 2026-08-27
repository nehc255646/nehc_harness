# Agent Harness — 实施计划（PLAN）

> 配套文档：`DESIGN.md`（架构总览）。本文档是实施层面的细化：已锁定决策、核心机制、数据模型、WS 协议、里程碑任务分解与验收标准。
> 定位：**纯个人使用、单机、单用户、多会话/多 agent 并发**的 Web 终端 coding agent。简历项目，保留 LangChain 作为能力展示点。

---

## 0. 目标与边界

**目标**：一个可本地运行、能持续工作、可中途拉用户确认、可派生子 agent 临时对话的 coding agent。

**非目标**（明确不做，控制范围）：
- 多用户/鉴权/租户隔离
- 横向扩展、分布式部署
- 完整的 LangGraph/AgentExecutor 编排（刻意手写 loop）
- 生产级安全沙箱（本机 subprocess + 用户门兜底）
- 多 agent 模型限流与成本核算（单用户共享 key，已知限制）
- 多会话工作目录写冲突隔离（并发 agent 可能同时编辑同一文件，单用户场景可接受，声明为已知限制）

---

## 1. 已锁定决策

| 决策点 | 结论 |
|---|---|
| 工具/模型层 | **保留 LangChain**（`@tool` + `ChatOpenAI`），编排手写 asyncio loop |
| 工具调用协议 | **原生 function-calling**（结构化 tool_calls） |
| 规模 | 单用户本机，多会话/多 agent 并发；**后端单进程单 worker**（进程内注册表，硬约束，见 §9） |
| 运行环境 | **Ubuntu VM（VMware）内全栈运行**；MySQL/Redis 使用本机已有实例（docker-compose 仅备选）；Windows 侧仅 VS Code Remote-SSH 开发 + 浏览器访问 VM IP；演示/危险测试前打快照 |
| 安全模型 | **安全由用户承担**：所有非放行的工具调用首次执行均弹审批，支持「本次会话同类命令放行」；配置文件持久放行规则；极简黑名单作安全网（默认开、可配置关） |
| 上下文管理 | 摘要压缩 + 滑动窗口 |
| 子 agent 隔离 | 主 agent 自生成"行为描述" + 精简历史快照；分两类：**交互型**（无工具、与用户对话）与 **工作型**（与主 agent 同等工具/审批权限，后台并发） |
| 工作型子 agent | 与主 agent 同等命令权限，并发后台运行；前端仅显示工作列表（无详细信息）；作为工具 `spawn_worker` 交给主 agent，需约束使用防全量转包；暂不支持递归 |
| 生命周期 | 显式状态机 + Redis 状态 + WS 断线恢复；**进程崩溃为已知限制**（见 2.5） |
| 模型配置 | **供应商分组**（Provider → 多 Model），按供应商分类存 DB；全部手动创建，仅 `api_key` 认证，无自定义请求头；提供 `hello` 探测按钮（可选测试，探测失败仍可保存，由用户决定是否保存）；新建会话模型解析优先级：**上一次使用 > 用户设置的兜底模型 > 留空**（兜底模型由用户在模型管理中自定义选择，可空） |

**技术线**：Python 3.14 + FastAPI(async) + LangChain 1.x + SQLAlchemy 2.0(async) + Alembic + MySQL 8 + Redis；React 18 + TS + Vite + Tailwind + zustand；WebSocket。

---

## 2. 核心机制细化

### 2.1 编排 loop（每轮迭代）

```
loop(session):
  while True:
    1. drain 本 agent 队列（asyncio.Queue）：
       用户新消息 / 子agent结果 → 注入本轮上下文
       (审批恢复不走队列，直接 future.set_result，见 2.2)
    2. 构造 messages = system + 摘要 + 滑动窗口(最近N个turn) + 本轮新增
    3. 调模型(ChatOpenAI.bind_tools) → tool_calls | text | 二者并存
    4. if 含 tool_calls:
         文本部分先流式推前端（message.delta，独立成条），再分发工具：
         并行分发: 对每个 call 走 policy(call)（见 2.2 用户门）
           放行（配置规则/会话规则/只读）→ 执行 (立即)
           黑名单 → 结果=[拒绝] (立即)
           其余   → 挂起审批 Future (不阻塞其他工具)
         asyncio.gather 等全部就绪(含审批 Future)后，统一一次性回填 → goto 2
         若其中含 finish_task → 标记 done，结束 loop
    5. else (纯文本):
         流式推前端，进入 idle，阻塞在 queue.get() 等下一事件
```

关键点：
- **队列只承载"下一轮输入"**（用户消息、子 agent 结果）；审批恢复是唤醒当前 await 的 Future，**不走队列**——两者是不同通信原语，不能混。
- **同轮多 tool_calls 语义原子**：全部就绪（执行完或批准/拒绝）后一次性回填，避免同一轮多次调模型、浪费 token。
- **文本与 tool_calls 并存**：文本部分先流式推送、作为独立 message 展示落库，随后进入工具轮。
- **drain 只发生在每轮开头**（即原子回填之后）：工具轮进行中到达的用户消息必然等本轮结束才被消费，前端对此显示"处理中"。
- `done` 判定：显式 `finish_task()` 工具、用户 `agent.stop`、**max 轮数兜底（超限 → done，区别于 error）**。
- `error` 判定：模型连续失败（重试耗尽）、未捕获异常 → error 态（见 2.5）。
- `idle`（等下一个输入）与 `done`（本轮结束）语义分开：idle 由新用户消息唤醒；**done 是终态但非死局**——done 后收到 `message.send` 开启新 run（状态回 running），上下文从 DB 历史 + 摘要重建。
- 队列消费用 `asyncio.Queue`（内存）+ Redis 备份（断线恢复）。
- 每轮 messages 构造逻辑集中在 `context.py`，与模型调用解耦。

### 2.2 用户门（policy/gate —— 安全自动由用户承担）

原则：不做命令语义级安全分析，**一切以用户审批为准**；系统只提供两层加速与一层兜底：

```
policy(call):
  命中极简黑名单（默认开，可配置关闭）          → 直接拒绝，decision=blocked
  命中配置文件放行规则（allow_rules.yaml，持久） → 直接执行，decision=config_allow
  命中会话放行规则（本次会话内累积）            → 直接执行，decision=session_allow
  只读工具 read/glob/grep                      → 默认放行（配置可改为需审批）
  其余（含全部 write/edit/shell 首次执行）      → 挂起审批，前端三选一：
        [执行一次]               → approved_once
        [本次会话同类命令均执行]  → approved_similar，同时写入会话放行规则
        [拒绝]                   → rejected
```

- **同类判定（定稿）**：固定取命令前 2 个 token 的前缀匹配（批准 `git push` 后，本次会话所有 `git push *` 放行；`git reset --hard` 不受影响）。工具类调用（write/edit 等）的"同类" = 同一工具名。规则结构 `{kind: shell_prefix | tool, pattern}`，可序列化。
- **作用域**：会话级，主 agent 与子 agent 共享；存 Redis `session:{id}:allow_rules`，断线重连经 `session.hello` 推回；新会话重置。
- **极简黑名单**（安全网，防"同类放行"误伤破坏性命令）：默认集覆盖常见破坏性命令（`rm -rf`、`mkfs`、`dd of=/dev/*`、`sudo rm`、`chmod -R 777 /`、`shred`、`wipefs`、`:(){ :|:& };:` fork 炸弹等），按 bash 语义以 `;`、`&&`、`||`、管道拆分子命令后逐段做 token/正则匹配；**明确声明非命令语义级**，组合绕过风险对个人使用可接受；可配置整体关闭。
- 审批挂起是 **per-agent** 的 Future，一个 agent 卡审批其它照跑；同轮全部工具就绪后统一回填（见 2.1）。
- `reason` 纯模板生成（工具名 + 参数摘要，超长截断），**不额外调模型**；完整参数走 REST 详情接口。
- 超时/WS 断连兜底：默认拒绝并记日志，通过 `approval.resolved(approved=false, reason=timeout|disconnect)` 通知前端。
- 审批结果写 ToolLog.decision，审计可查（枚举见 §4）。

### 2.3 上下文管理（摘要 + 滑动窗口）

- **turn 定义（定稿）**：从一次唤醒事件（用户新消息 / 子 agent 结果回投）唤醒 loop 起，到 loop 回到 idle/done 为止的全部消息为一个 turn。滑动窗口按 turn 计数，保留最近 `N` 个 turn（默认 N=20，可配）完整消息。
- **摘要**：用 `tiktoken` 估算总 token，达到 `SUMMARY_TOKEN_RATIO × Model.context_window` 时触发（而非按条数——工具结果可能条数少但 token 巨大）；由**当前会话同一模型**生成，计入该轮耗时与配额；失败降级 = 放弃本轮摘要、直接丢最旧窗口外内容并告警，不阻塞 loop。
- **裁剪顺序（定稿）**：先按 N 保底裁剪（保最近 N 个 turn）→ token 触发时把窗口外更早轮次合并进摘要 → 合并后仍超标则从窗口尾丢弃最旧 turn。条数窗口与 token 阈值独立生效。
- **缓存失效**：用"覆盖到第几轮"的版本号标记，随轮次滚动做**增量合并**（增量合并为 M4 优化；M3 先用"旧摘要 + 新滑出消息"直接合并，避免为不存在的瓶颈提前复杂化）。
- **大结果截断（定稿）**：单条工具结果超过 `MAX_TOOL_RESULT_TOKENS`（默认 8K，可配）时，保留头尾 + 省略标记注入上下文，完整结果落 ToolLog 供 REST 查询；单条消息超整个窗口时同样处理并告警。
- 注入顺序：`system + 摘要 + 窗口消息 + 本轮`。
- 子 agent 快照复用同一套 `context.py` 的裁剪逻辑。

### 2.4 子 agent（单次、隔离、异步回投）

子 agent 分两类，复用同套快照/隔离/回投与生命周期，仅工具与交互面不同：

**A. 交互型**（与用户临时对话，M2 首批）

```
主agent调 spawn_subagent(behavior_desc, goal)
  → 后端快照: 精简主历史 + behavior_desc + 本次所有历史子agent记录
  → 冻结上下文，生成独立 task + 前端侧栏面板(subagent.session)
   → 子 agent 是纯对话代理：无工具（仅一个收敛信号 finish_subagent），不走 policy/用户门
  → 子 agent 有独立 Queue（收用户侧栏消息）
  → 用户对话 / 子 agent 调 finish_subagent(summary) 收敛
  → 结果异步投回主 agent 队列（与用户消息同一条唤醒路径）
  → 子 agent task 销毁(单次)
时序兜底: 主agent若已结束 → 结果标"迟到结果"存 SubAgentRun，续聊时再喂回
```

**B. 工作型**（主 agent 主动派生的后台工作者，与用户呼出型不同）

```
主agent调 spawn_worker(task, constraints?)
  → 后端快照: 同交互型快照逻辑（精简历史 + behavior_desc + 约束）
  → 冻结上下文，生成独立 task（后台运行，默认不弹侧栏）
  → 工作型与主 agent 同等命令权限：工具集与 policy/用户门完全一致（会话级共享 allow_rules），并发后台执行
  → 前端仅在“工作区”显示工作列表（id/任务摘要/状态 running/done/error，无详细日志），不需要详细信息
  → 工作型自主工作至 finish_worker(result) 或触顶/超时强制收敛 → 结果暂存（按 batch_id 分组，同一轮派生的 N 个为一批）
  → 聚合回投：同一 `batch_id` 的工作型全部结束（done/error/超时）后，在主 agent 此后完成的第一个节点边界以单条聚合事件 `worker.batch_done{batch_id, workers[]{id,status,result}}` 一次性批量注入（与交互型逐条回投不同）
  → 约束：防“全量转包”——见下方；暂不支持递归（工作型不可再调 spawn_worker/spawn_subagent）
  → 提示词：工作型独立 `WORKER_SYSTEM_PROMPT`（`agent/prompts.py`）+ 工具描述双重约束
  → 时序兜底同交互型：主 done 后结果标迟到，仅存 SubAgentRun；error/超时默认按完成处理并随批量回投
```

- **`awaiting_subagent` 是纯展示标记**，不阻塞 loop：主 agent 派生子 agent 后无别的任务即正常进 idle，交互型逐条回投、工作型按 `batch_id` 聚合为单条 `worker.batch_done` 回投均经队列唤醒——与"收到用户新消息"走同一条路径；工作型批量注入发生在主 agent 下一个节点完成边界。
- **上限**：两类子 agent 均复用 `MAX_ROUNDS` + `WORKER_TIMEOUT`(默认 600s) 与总 token 上限，触顶/超时强制收敛并提示用户；工作型 error/超时按完成处理，随批量一并回投（默认工作结束即聚合）。
- **主 agent 已 done 时**：侧栏仍可与交互型对话至 finish_subagent；工作型已在后台完成则标迟到，仅存 SubAgentRun，续聊时喂回，**不主动唤醒**主 agent。
- **并发上限**：交互型与工作型**共享** `SUBAGENT_MAX_CONCURRENCY`（默认 3，可配）；关闭交互型面板仅收起 UI，不终止会话；工作型无面板，终止 = `finish_worker` 或对该子 agent 发 `agent.stop`。
- **messages 构造**：快照即初始 messages，此后纯追加；超限走 `context.py` 同一套裁剪逻辑；工作型提示词独立于主 agent/交互型。
- **约束使用（防全量转包）**：`spawn_worker` 工具描述 + 独立工作型 prompts 双重约束——仅当任务可拆为**独立子任务**且并行收益明显时才派生；单轮派生不超过 `MAX_WORKERS_PER_TURN`（默认 2），总并发不超过 `SUBAGENT_MAX_CONCURRENCY`；主 agent 仍需保留核心编排与聚合职责，禁止将整轮工作一次性转包。
- 交互型无工具是**有意的范围切割**，工作型则完整复用主 agent 工具链；两者实现上工作型为“完整 loop（含审批）”，交互型为“轻量对话 + 单个 finish 工具”。

### 2.5 生命周期状态机

```
idle → running → awaiting_approval
                → awaiting_subagent (展示态，不阻塞 loop)
running → done | error
```

- **error 态**：模型连续失败（重试耗尽）、未捕获异常 → error；error 态可由新用户消息重启 loop。max 轮数超限归 done（区别于 error）。
- **done 为终态但非死局**：done 后收到 `message.send` 开启新 run（状态回 running）。
- `paused` 状态在 M1/M2 **不实现**（无人触发）；如后续做"暂停"，只在**自然迭代边界**实现，不中断 in-flight 模型调用/工具执行。
- 状态写入 Redis `agent:{id}:state` (TTL 续期)；**MySQL 为唯一权威**：Redis TTL 过期不作为结束判定依据，`session.hello` 时从 DB + 内存 AgentManager 合成真实状态并回写 Redis。

**断线恢复**：
1. 前端重连，握手 `session.hello`。
2. 载荷：`session_id`、当前 `agent_state`、**完整 pending 审批列表**（approval_id/tool/args/reason）、**会话放行规则列表**（session_allow_rules）、**活动中的 subagent 面板清单**。
3. 前端先渲染历史，再对每条 pending 逐条弹确认卡，并重开子 agent 面板。
4. 正在流式的半条 message 标记"未完成"，重连后重新流式。
5. agent loop 不中断继续跑；若审批超时，默认拒绝并通过 `approval.resolved(reason=timeout)` 通知。

**进程崩溃（已知限制）**：单进程内存中的在途 loop / 审批 Future / 内存队列随崩溃丢失。恢复边界 = **MySQL 已落库历史**：用户重发消息后开启新 run，从 DB 历史 + 摘要重建上下文（任务见 M3）。运行约束：`uvicorn --workers 1`。

---

## 3. WS 事件协议（草案定稿）

### Server → Client
| 事件 | 载荷 |
|---|---|
| `session.hello` | session_id, title, agent_state, pending_approvals[], session_allow_rules[], subagent_panels[] |
| `message.start` | agent_id, message_id, role |
| `message.delta` | agent_id, message_id, delta |
| `message.done` | message_id, role, content |
| `tool.start` | call_id, name, args |
| `tool.result` | call_id, result, is_error |
| `approval.request` | approval_id, tool, args, reason |
| `approval.resolved` | approval_id, approved, reason(user\|timeout\|disconnect) |
| `subagent.opened` | subagent_id, kind(interactive\|worker), session_id |
| `subagent.done` | subagent_id, kind, result_summary |
| `worker.status` | workers[]{subagent_id, task_summary, state(running\|done\|error)} — 仅列表，无详细信息 |
| `worker.batch_done` | batch_id, workers[]{subagent_id, status, result} — 批量聚合回投（单事件） |
| `agent.state` | agent_id, state |
| `error` | code, message |

> `message_id` 由**后端**在 `message.start` 分配（最终落库为主键），前端从首个事件拿到后再追加 `message.delta`；delta 粒度跟随上游模型流式（token/chunk），不自拆字符。消息展示排序按**自增 id**（不用 created_at，避免撞时间戳）。

> **心跳**：ping/pong 每 30s，连续 3 次未响应判死连接。
>
> **错误码集**（`core/errors.py` 定稿）：`MODEL_ERROR` / `MODEL_TIMEOUT` / `TOOL_ERROR` / `APPROVAL_TIMEOUT` / `SESSION_NOT_FOUND` / `INTERNAL`；前端按 code 映射展示文案。
>
> **多标签页**：事件向该会话的所有连接广播；`approval_id` 单次消费，第二个响应返回 error 事件。

### Client → Server
| 事件 | 载荷 |
|---|---|
| `message.send` | session_id, content |
| `approval.response` | approval_id, decision(approve\|approve_similar\|reject) |
| `subagent.response` | subagent_id, content |
| `session.create` | (title) |
| `session.select` | session_id |
| `session.delete` | session_id |
| `agent.stop` | agent_id |

> **`agent.stop` 完整语义**：取消当前 await 点 → 回收 shell 进程组（`start_new_session=True` 建立的进程组，SIGTERM → 超时 SIGKILL）→ 清空队列 → in-flight 工具以 `is_error="stopped"` 回填 → pending 审批一律 resolve(rejected) → 状态置 done。

---

## 4. 数据模型

### MySQL（唯一权威）
- **Session** {id, title, status(active|archived|deleted), model_id FK→Model.id NULL, created_at, updated_at}
  - 删除为软删（status=deleted），删除前先对该会话活动 agent 执行 stop 语义；`model_id` 记录本会话选用模型；新建时按优先级解析：**上一次会话的 model_id（按 `updated_at` 最新一条）> AppConfig.default_model_id（用户自定义兜底，可空）> 留空**；后期 env 自动导入仅在库为空时创建 Provider/Model，不自动设兜底
- **Message** {id, session_id, agent_id, role, content(json), tool_call_id?, created_at}
  - assistant 的 tool_calls 定义（name + args JSON）存 content(json)；tool 结果行凭 `tool_call_id` 与 assistant 行分组
  - 展示排序按自增 id
- **ToolLog** {id, session_id, message_id, tool_call_id, agent_id, name, args(json), result(json), is_error, duration_ms, rule_hit, decision, created_at}
  - `decision` 枚举：`config_allow / session_allow / approved_once / approved_similar / rejected / blocked / timeout`（只读白名单放行记 config_allow）
  - 被 rejected / blocked / timeout 的调用**同样落 ToolLog**（result 记原因），审计完整
- **SubAgentRun** {id, main_session_id, subagent_id, kind(interactive|worker), behavior_desc, goal, status, result, created_at, finished_at, late} — 工作型与交互型共表，以 `kind` 区分；工作型 `result` 为结构化工单摘要
- **Provider** {id, provider_id(slug, unique), display_name, base_url, api_key_encrypted, created_at} — 按供应商分组，api_key 加密存储（Fernet，密钥放 .env:ENCRYPTION_KEY），仅 api_key 认证，全部手动创建
- **Model** {id, provider_id FK→Provider.id, model_id(实际请求串), display_name, context_window, temperature, created_at, UNIQUE(provider_id, model_id)} — 摘要触发阈值基于 Model.context_window；保存前以 `hello` 探测
- **AppConfig** {id, key, value, updated_at} — 单例配置表，当前仅 `key='default_model_id'` 存用户自定义兜底模型 `Model.id`（可空，FK 校验；删除对应 Model 时置空）

外键（session_id / agent_id / message_id）建索引。

**放行规则不入库**：持久规则在 `allow_rules.yaml`（配置文件），会话规则在 Redis。

### 落库点（定稿）
- `message.done` 时整条落库；tool 结果随 `tool.result` 落 ToolLog
- 失败兜底：内存待写队列，loop 下次 drain 时补写
- 一致性边界：进程崩溃最多丢失最后一条未落库消息（前端标记"未完成"）；验收承诺为「**崩溃后已落库消息不丢**」

### Redis（实时态）
| Key | 内容 |
|---|---|
| `agent:{id}:state` | 状态机 (TTL，过期不作结束判定) |
| `agent:{id}:pending_approvals` | pending 审批列表 (TTL) |
| `session:{id}:queue` | 事件队列备份 |
| `session:{id}:allow_rules` | 会话放行规则 |
| `ctx:{session_id}:summary` | 上下文摘要缓存 |

---

## 5. 目录结构（定稿）

```
Harness/
├── DESIGN.md / PLAN.md
├── .env.example
├── allow_rules.yaml                # 持久放行规则
├── docker-compose.yml              # 预留（本机已有 MySQL/Redis 则不用）
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── tests/
│   │   ├── unit/                   # policy/context/loop/rules
│   │   └── integration/            # WS 流/重连/stop 回收
│   └── app/
│       ├── main.py                 # FastAPI 入口，挂 WS 路由
│       ├── api/ws.py               # WebSocket 端点（心跳/广播/单次消费）
│       ├── api/rest.py             # REST：会话/历史/Provider·Model 配置（分组）/审批详情
│       ├── core/theme.py             # 预留：主题色常量（与前端 CSS 变量对齐）
│       ├── agent/
│       │   ├── loop.py             # 手写 asyncio loop（含工作型完整 loop，走同套 policy/审批）
│       │   ├── subagent.py         # 子 agent 快照/隔离/回投（分交互型轻量对话与工作型完整 loop，暂不支持递归）
│       │   ├── manager.py          # AgentManager 注册表（进程内）
│       │   ├── context.py          # 摘要+滑动窗口裁剪+大结果截断
│       │   ├── prompts.py
│       │   └── executor.py         # ChatOpenAI 封装 + 重试退避
│       ├── tools/
│       │   ├── files.py            # read/write/edit/glob/grep
│       │   ├── shell.py            # subprocess 进程组启动(start_new_session)/killpg 回收
│       │   ├── subagent_tool.py    # spawn_subagent / finish_subagent（交互型）
│       │   ├── worker_tool.py      # spawn_worker / finish_worker（工作型，与主 agent 同权限，后台并发，工具描述含防全量转包约束）
│       │   └── registry.py         # 工具注册表（含 agent 级 finish_task）
│       ├── permissions/
│       │   ├── policy.py           # 用户门分流：黑名单/配置规则/会话规则/审批
│       │   ├── rules.py            # 规则解析与前缀匹配
│       │   └── gate.py             # per-agent 审批挂起/恢复
│       ├── core/
│       │   ├── config.py           # pydantic-settings（全量配置见 §10）
│       │   ├── errors.py           # 错误码定义
│       │   ├── logging.py          # 日志（文件轮转可选）
│       │   └── redis.py
│       ├── models/                 # SQLAlchemy ORM
│       └── schemas/                # Pydantic
└── frontend/
    ├── package.json / vite.config.ts   # dev proxy /ws → localhost:8000
    └── src/
        ├── api/ws.ts
        ├── store/                  # zustand（WS 事件流 → store 订阅）
        ├── components/{Chat,ToolCallCard,ApprovalModal,CodeDiff,SessionSidebar}.tsx
        ├── components/subagent/SubAgentPanel.tsx
        └── hooks/useAgentStream.ts
```

---

## 6. 里程碑任务分解与验收

### M1 — 核心闭环
任务：
- [ ] 脚手架：backend pyproject/FastAPI 启动、frontend Vite 初始化
- [ ] `core/config.py` 读 .env；`core/redis.py`（连不上仅告警）；`core/logging.py`、`core/errors.py`
- [ ] `agent/executor.py`：ChatOpenAI + bind_tools 封装 + 失败一次指数退避重试（次数可配）
- [ ] `agent/loop.py`：基础 loop（无审批）能跑 read/write/glob/grep；处理文本与 tool_calls 并存
- [ ] `tools/files.py` + `tools/registry.py`（含 agent 级 finish_task）
- [ ] `permissions/rules.py` + `policy.py` + `gate.py`：极简黑名单 + 配置/会话放行 + 只读放行 + 三选审批挂起/恢复（统一回填）
- [ ] `api/ws.py` + `api/rest.py`：会话流打通 + ping/pong 心跳 + 错误码
- [ ] 前端 `ws.ts` + zustand store + `Chat.tsx` + `ToolCallCard.tsx` + `ApprovalModal.tsx`（三按钮）+ `useAgentStream.ts`；主题基座：暗色（黑色）打底 + 青色强调色（Tailwind dark + CSS 变量 `--color-accent`，后期可切换）

**验收**：输入一句话任务 → 首次命令弹三选审批 → 选「同类执行」后同类免批 → 黑名单命中直接拒 → 结果流式渲染。

### M2 — 子 agent（交互型 + 工作型，影响功能运行故与 M1 同步设计、M2 一并交付）
任务：
- [ ] `tools/subagent_tool.py`：spawn_subagent / finish_subagent（交互型，无工具，与用户对话）
- [ ] `tools/worker_tool.py`：spawn_worker / finish_worker（工作型，与主 agent 同等工具/审批权限，后台并发，工具描述含防全量转包约束，暂不支持递归）
- [ ] `agent/subagent.py`：快照/隔离/独立 task/异步回投 + MAX_ROUNDS 与 token 上限强制收敛（工作型走完整 loop 含 policy/审批，交互型走轻量对话）
- [ ] `agent/context.py`：快照裁剪 + 行为描述注入（含工作型 task 约束注入 + prompts.py 防全量转包提示）
- [ ] 前端 `SubAgentPanel.tsx`：侧栏面板（交互型）、并发上限（默认 3，交互型与工作型共享）、迟到结果标记、关面板≠终止
- [ ] 前端 `WorkerStatus.tsx`：后台工作列表区域（显示 workers[]{id/任务摘要/状态}，无详细信息，青色状态指示）

**验收**：交互型：主 agent 中途派生 → 侧栏开面板用户对话 → 结果异步回投 → 主 agent 下一迭代点消费，工作型：主 agent 派生 spawn_worker → 后台并发执行（走同等审批）→ `worker.status` 列表可观测 → 结果回投聚合；两者均满足主 done 后迟到标记；约束生效（单轮不超过 MAX_WORKERS_PER_TURN=2，不出现全量转包）。

### M3 — 持久化
任务：
- [ ] 建专用库与用户：`CREATE DATABASE harness` + `harness`@localhost 最小授权（不直接使用 root）
- [ ] SQLAlchemy 模型 + Alembic 迁移（含外键索引）：Provider/Model 两表（provider_id slug 校验、api_key 加密、联合唯一）
- [ ] MySQL 连接 + 会话续聊（落库点定稿：message.done / tool.result；内存待写队列兜底）
- [ ] **run 从 DB 历史 + 摘要重建上下文**（支撑 done/error 后重启与崩溃恢复路径）
- [ ] `api/rest.py`：历史查询、Provider/Model CRUD（按供应商分组、全部手动创建）、`POST /api/providers/{id}/test` 以 `hello` 探测（仅 api_key，可选测试，失败仍可保存）、`GET /api/models/resolved-default` 按“上一次使用 > 兜底”解析默认模型、`GET/PUT /api/config/default-model` 读写用户自定义兜底模型、审批详情接口
- [ ] `agent/executor.py` 接入 Provider/Model：按 `Session.model_id → Model → Provider.base_url + api_key + model.model_id` 实例化 ChatOpenAI；未选模型时前端阻拦发起，新建会话预填 `resolved-default`

**验收**：重启后端后会话历史仍在，可续聊；**杀进程后已落库消息不丢**。

### M4 — Redis 全面（WS 断线恢复）
任务：
- [ ] agent 状态机写入 Redis + TTL（MySQL 权威对账：hello 时合成真实状态回写）
- [ ] pending 审批 + 会话放行规则落 Redis，前端重连重推（hello 带 session_allow_rules）
- [ ] 断线恢复协议（`session.hello` 对账）
- [ ] 上下文摘要缓存 + 增量合并

**验收**：前端刷新/断线重连，进行中审批不丢、会话放行规则仍在、agent 不永久挂起。

### M5 — 打磨
任务：
- [ ] CodeDiff.tsx 文件 diff 展示（依赖 M3 落库的 write/edit ToolLog 结构化数据）
- [ ] 多会话侧栏 SessionSidebar.tsx
- [ ] 主题色切换：`ThemeProvider` + zustand `theme` store + CSS 变量切换（青色为默认，后期可扩展）
- [ ] 测试（用例清单见 §8）
- [ ] 可选：`tool.progress` 事件（长命令尾部输出节流推送）
- [ ] 文档、README、demo 脚本

**验收**：跑通端到端 demo。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| LangChain/pydantic 兼容性（py3.14 + LangChain 1.x） | 核心依赖在 3.14 下已实测可装可导入（langchain 1.3 / pydantic 2.13 / SQLAlchemy 2.0.52）；注意 1.x API 变更（如 ChatOpenAI 导入路径），M1 脚手架先做最小模型调用冒烟测试，pyproject 锁定版本 |
| 审批 Future 泄漏导致 agent 永久卡死 | 强制超时 + WS 断连兜底（M1 就做） |
| 进程崩溃丢在途状态 | 单进程已知限制；恢复边界 = DB 已落库历史 + 重发消息新 run（见 2.5 / M3） |
| 共享模型 key 无限流 | 单用户已知限制，记录即可 |
| agent.stop 孤儿/僵尸子进程 | shell 以进程组启动，stop 时 `os.killpg`（SIGTERM→SIGKILL）整体回收；单测覆盖 |
| agent 破坏性操作误伤环境 | 运行于 Ubuntu VM，演示/危险测试前打 VM 快照，与用户门形成双保险 |
| 会话放行规则过粗导致误放行 | 前缀粒度固定 2 token + 极简黑名单兜底；安全责任在用户（设计取向） |
| 长期运行上下文爆炸 | M1 就留 `context.py` 抽象，M3/M4 接入摘要 |
| MySQL 未启动 | 后端启动降级（仅内存运行），启动时检测并告警 |
| 模型未配置/连不上 | 启动检测，前端给清晰错误；默认本地 OpenAI 兼容 |

---

## 8. 测试策略

- **单元**：
  - `rules`：黑名单拆分绕过边界（`;`/`&&`/管道组合）、前缀匹配粒度
  - `policy`：四路分流（黑名单/配置/会话/只读）正确性
  - `context`：turn 定义裁剪、摘要触发与丢弃顺序、大结果截断
  - `loop`：mock 模型、文本+tool_calls 并存、事件注入、同轮批量回填
- **集成**：
  - WS 流：三选审批 → 批准 → 恢复 → 结果；审批超时/重复响应
  - `agent.stop`：进程组回收、in-flight 工具 stopped 回填、pending 审批清理
  - 并发 agent 隔离（一个卡审批其它照跑）
  - 重连 `session.hello` 对账（含 session_allow_rules 重推）
  - 大结果截断 + REST 完整查询
  - 杀进程后落库一致性
- **手动 demo**：一个端到端脚本验证 M5 验收

---

## 9. 启动方式（Ubuntu VM 内）

1. VMware Ubuntu 虚拟机：代码仓放 VM 的 ext4（**勿放共享目录**）
2. MySQL/Redis 使用本机已有实例（MySQL 8.4 localhost:3306、Redis localhost:6379 已运行），**docker-compose 仅在无本机实例时启用**
3. Windows 侧：VS Code Remote-SSH 连入 VM 开发；防火墙放行端口：`sudo ufw allow 5173/tcp && sudo ufw allow 8000/tcp`；`.env` 中 `VITE_WS_URL` 指向 VM IP
4. backend：`uv sync` → `alembic upgrade head` → `uvicorn app.main:app --workers 1`（**单进程硬约束**：AgentManager 为进程内注册表，多 worker 会破坏寻址与审批路由）
5. frontend：`npm install && npm run dev --host`
6. Windows 浏览器访问 `http://<VM_IP>:5173`
7. 跑 demo / 让 agent 执行危险操作前先打 VM 快照

---

## 10. 配置项清单（pydantic-settings + allow_rules.yaml，同步 .env.example）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MYSQL_PASSWORD` | — | MySQL 密码；本机 root 密码现由环境变量 `MYSQL_PWD` 提供，建议 M3 建专用用户 harness 后改用之 |
| `ENCRYPTION_KEY` | — | api_key 加密密钥（Fernet，`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成） |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME` | — | 已废弃，模型配置改由 DB 的 Provider/Model 管理（M3）；保留仅作本地临时调试备选 |
| `REDIS_URL` | localhost:6379 | 连不上仅告警 |
| `APPROVAL_TIMEOUT` | 120s | 审批超时（超时默认拒绝） |
| `SHELL_TIMEOUT` | 300s | shell 工具单条命令执行超时（超时 killpg 回收，结果标记超时） |
| `WORKDIR` | 项目下 workspace/ | agent 文件操作与 shell 执行的工作根目录约束 |
| `MAX_ROUNDS` | 50 | 单 run 最大轮数（超限 → done；子 agent 复用） |
| `WINDOW_N` | 20 | 滑动窗口 turn 数 |
| `SUMMARY_TOKEN_RATIO` | 0.65 | 摘要触发阈值 × context_window |
| `MAX_TOOL_RESULT_TOKENS` | 8192 | 单条工具结果截断阈值 |
| `RETRY_COUNT` | 1 | 模型调用失败重试次数 |
| `SUBAGENT_MAX_CONCURRENCY` | 3 | 每会话子 agent 并发上限（交互型与工作型共享） |
| `MAX_WORKERS_PER_TURN` | 2 | 单轮派生工作型上限（防全量转包，按 batch_id 分批） |
| `WORKER_TIMEOUT` | 600s | 工作型子 agent 整体超时（超时按完成随批量回投） |
| `HEARTBEAT_INTERVAL_S` | 30 | WS 心跳间隔（3 次判死） |
| `READONLY_NEED_APPROVAL` | false | 只读工具是否也需审批 |
| `BLACKLIST_ENABLED` | true | 极简黑名单开关 |
| `ALLOW_RULES_FILE` | allow_rules.yaml | 持久放行规则文件路径 |

`allow_rules.yaml` 模板：

```yaml
allow_shell:            # 前 2 token 前缀匹配
  - "git status"
  - "npm run build"
allow_tools:
  - read
  - glob
  - grep
```

---

## 下一步

从 **M1** 开始。先搭脚手架 + executor + 基础 loop（含用户门），打通 WS 极简聊天，达成 M1 验收后进入 M2。
