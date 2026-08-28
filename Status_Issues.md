# 状态与问题记录

## 已知限制 (按 PLAN)
- 模型未配置时走 heuristic 演示，真实 LLM 需配置 OPENAI_API_KEY/BASE_URL (或 M3 DB)
- 持久化/Redis全面 待 M3-M4
- 进程崩溃丢在途状态为已知限制 (单进程 --workers 1)

## M2 — 子 agent (已完成 2026-08-28)
- 交互型 `spawn_subagent`：快照(精简历史+behavior_desc+任务) → 隔离上下文+独立 task → 侧栏面板 `subagent.opened` → 仅 `finish_subagent` 工具，不走用户门 → 独立 Queue 收 `subagent.response` → `finish_subagent` 异步回投主队列 → 销毁；主 done 后标 late
- 工作型 `spawn_worker`/`spawn_workers`：同快照逻辑，后台并发，与主 agent 同等工具/审批(会话级共享 allow_rules) → `worker.status` 列表推送 → `finish_worker` 或 MAX_ROUNDS/WORKER_TIMEOUT(600s) 触顶 → 按 `batch_id` 暂存，同一批全部结束后在主 agent 首个节点边界以单条 `worker.batch_done` 聚合注入；并发共享池 SUBAGENT_MAX_CONCURRENCY=3，单轮 ≤2，防全量转包(工具描述+WORKER_SYSTEM_PROMPT双约束)，暂不支持递归；error/超时按完成随批量回投
- 快照裁剪复用 `context.build_messages` + `window_n` 窗口；隔离为冻结快照，不共享可变状态
- 后端：`agent/subagent.py` 完整实现(Interactive/Worker 两类 loop、快照/隔离/回投/批量聚合/并发限流/超时)、`tools/subagent_tool.py`/`worker_tool.py` 注册、`agent/loop.py` 支持 spawn_* 分发(不走 policy，直接执行)+`worker_batch_done`/`subagent_result` 回投处理+回投消息去重防无限递归、`agent/prompts.py` 新增 `INTERACTIVE_SYSTEM_PROMPT`/`WORKER_SYSTEM_PROMPT`、`api/ws.py` 补 `subagent.response` 转发+`hello` 带 `subagent_panels/workers` 对账
- 前端：`store/agentStore.ts` 增加 `subPanels/workers` + `sendSubagentMessage` + 事件处理(`subagent.opened/done/worker.status/batch_done/subagent.message`)、`SubAgentPanel.tsx` 侧栏交互(输入+状态+迟到标记)、`WorkerStatus.tsx` 后台列表(青色指示)、`api/ws.ts` 已支持 ping/pong
- 修复：工作型批量聚合并发双重回投竞态(先检查长度再清批，无 await 间隙)、`loop` heuristic 对回投聚合消息去重(避免批量触发新派生)、派生优先级提升至 shell 之前
- 验证：`ruff`/`tsc --noEmit`/`eslint`/`pytest 21 passed`/`vite build` 通过；WS 脚本验证 交互型派生→侧栏对话→结果异步回投→主 agent 消费，工作型派生→后台并发→`worker.status` 可观测→`worker.batch_done` 聚合回投，约束生效(单轮≤2、总并发≤3)

## M2 审查修复 (2026-08-28)
- A1 `enqueue` 唤醒条件改为 `_task.done()`：done/error 终态后新消息/子 agent 回投均可唤醒新一轮 run（原 state==idle 判断导致 finish/stop 后 agent 永久失联，已实测复现）；子 agent 回投改传 bound `enqueue` 而非裸 queue，终态后回投同样可唤醒
- A2 纯文本与 tool_calls 分支拆分：tool_calls 路径文本仅随 `assistant_msg` 入 history（`_emit_message` 增 `record` 参数），heuristic 演示模式不再产生重复 assistant 文本
- A3 交互型子 agent 空闲等待重构：无新输入且末尾非 user 时阻塞等待 120s，超时自动收敛（`[空闲超时]`），不再每 120s 空转调模型烧 token
- A4 前端 StrictMode 双注册修复：`agentStore` handler 绑定幂等（模块级标记）；`HarnessWS.connect` 幂等（先 detach 旧连接），重连跟踪最新 sessionId（`setSession`），session.select 切换后断线不回连旧会话
- B1 glob 越权修复：结果逐条 resolve+relative_to 校验，内嵌 `../` 逃逸条目直接过滤（原仅检查 pattern 开头）
- B2 黑名单 rm 变体修复：`_rm_rf_hit` 按 token 判定递归+强制组合（覆盖 `-r -f`/`-fr`/`-Rf`/`--recursive --force`/sudo 前缀），原正则仅匹配 `-rf` 连写；删除死代码 `extract_shell_prefix`
- B3 `agent.stop` 支持子 agent：`subagent.stop_subagent`（拒绝其 pending 审批 + killpg + cancel + `[已停止]` 标记 done）；ws 端主 agent → 子 agent → 会话主 agent 三级兜底
- B4 `ENCRYPTION_KEY` 默认值清空 + 启动告警（原硬编码合法 Fernet key，泄漏风险）
- B5 子 agent 流式协议统一：真实模型路径改用 `message.start/delta/done` + `subagent_id`（与 heuristic 路径一致，补齐 `message.done`），废弃前端未实现的 `subagent.delta`；worker 流式文本不再因缺 `message.start` 被前端丢弃
- B6 `subagent.done` 双重广播去除（`_finish` 单点广播）
- C1 `kill_shell_group` SIGTERM→2s 后 SIGKILL 升级（threading.Timer，PLAN §3 语义）
- C2 新建 agent 初始广播 idle（原误报 running）；`wait_for(queue.get, None)` 改裸 await；spawn 预检查死代码/`done_count`/gate `default_factory` 清理；`subagent.py` 加 `from __future__ import annotations` 消除前向引用对 PEP 649 的隐式依赖
- C3 ws `broadcast` 并行 gather（单连接慢/死不阻塞同会话其他连接）
- C4 前端 `agentState` 入 store（hello/agent.state 对账），`sendMessage` 仅新 run 清空工具卡；SessionSidebar 会话可点击（`session.select`）
- D1 测试：修 3 个 ruff 告警（SIM117/RUF059），新增 6 项回归 — done 唤醒/emit record 不入 history/heuristic 文本去重/交互型 stop/单 worker 批次聚合清理/glob 逃逸过滤
- 验证：`ruff` 0 告警 / `pytest 27 passed` / `tsc --noEmit` / `eslint` / `vite build` 通过；WS 冒烟 hello=idle、多轮续聊、`rm -r -f` 拦截均通过

## 代码审查修复 (2026-08-28，M2 后全量审查)
- H1 迟到批次不唤醒：`_handle_worker_finish` 迟到路径仅广播 `worker.batch_done`/`worker.status` 供前端观测，不再注入主队列（原无条件 enqueue 会经 A1 唤醒逻辑拉起已 done 的主 agent，违反 PLAN §2.4）
- H2 流式 tool_calls args 修复：抽公共助手 `context.accumulate_tool_calls`/`parse_tool_calls`（dict/对象双形态兼容）；args 字符串碎片拼接、已解析 dict 整体覆盖（原 chunk 双属性覆盖导致 `json.loads(dict)` TypeError → `{"__raw":...}` → shell 空命令静默执行）；ainvoke 回退路径 dict args 直接保留（原 `str(dict)` 单引号解析失败）；loop/subagent 两处重复解析代码一并消除
- M1 子 agent 纯文本 history 双重追加去除：`_emit_text` 已入 history，纯文本分支不再重复 append
- M2 每轮派生上限真实生效：`_turn_spawned` 按轮累计（检查+累加无 await 原子），跨多次 `spawn_worker`/`spawn_workers` 调用共享额度；删除死字段 `_current_batch_id`/`_batch_spawned`
- M3 worker 超时孤儿进程：`SubAgentLoop.run` finally 统一 `kill_shell_group(subagent_id)`；`shell_async` 仅在进程已结束时移除 pgid 登记（取消时保留供外层回收）
- awaiting_approval 展示态：审批挂起/恢复时广播 `agent.state`（并行多审批以 pending 表判定、stop 路径不回 running）
- 杂项：ws 会话迁移空连接集清理（`_detach_connection`）；CORS 移除 credentials；`rules.py` yaml mtime 缓存；`config.py` 移除代码内默认 MySQL 密码
- 前端：子 agent 流式消息按 `subagent_id` 路由进侧栏面板对话（原混入主 Chat）；面板渲染对话记录/迟到标记/收起按钮（收起≠终止）；WorkerStatus 增加 running 项停止按钮；`sendSubagentMessage` 去本地回显（服务端统一广播防重复气泡）；hello 对账保留面板已有对话
- 测试：新增 7 项回归（碎片拼接/dict args 不损坏/迟到不唤醒/交互型 history 去重/每轮派生上限/取消 pgid 保留/）；验证 `ruff` 0 告警 / `pytest 34 passed` / `tsc` / `eslint` / `vite build` / WS 冒烟（派生→对话→回投→消费、黑名单拦截）全部通过

## 待办 M3
- SQLAlchemy + Alembic + MySQL 持久化 + Provider/Model 分组 + 会话续聊
