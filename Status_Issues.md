# 状态与问题记录

## M3 — 持久化 (已完成 2026-08-28)
- 建库 `harness` + 用户 `harness`@localhost；Alembic `0001_m3`：Provider/Model/AppConfig/Session/Message/ToolLog/SubAgentRun
- 落库点：用户消息 / `message.done`（含 tool_calls 回填）/ `tool.result`→ToolLog / 子 agent spawn+finish；失败入内存待写队列，loop drain 时 `flush_pending`
- 重启路径：`AgentLoop.hydrate_from_db` 从 Message 按自增 id + Session.summary 重建上下文；`Executor.from_session_id` 按 Session.model_id → Provider 解密 key
- REST：会话 CRUD/历史/tool-logs/审批详情；Provider/Model CRUD；`POST /providers/{id}/test` hello 探测（失败可仍保存）；`GET /models/resolved-default`（上一次使用 > 兜底 > 空）；`GET/PUT /config/default-model`；删除 Model 时清空兜底
- 库为空且配置了 OPENAI_* 时导入 env Provider/Model，不自动设兜底
- 前端：启动拉会话列表/历史续聊；顶栏选模型；无模型且已有模型列表时阻拦发送；模型/供应商设置弹窗
- MySQL 不可用降级内存运行；pytest 跨 loop 用 NullPool + 按 event loop 重建引擎
- 验证：`ruff` 0 告警 / `pytest 42 passed` / `tsc` / `eslint` / `vite build`；REST 会话+模型闭环单测

## M3 审查修复
- hydrate 后 `start()` 保持 idle，等用户消息/回投再调模型（不自动续跑已落库 transcript）
- 同轮先落 assistant `Message` 再写 ToolLog，保证 `message_id` 可关联
- `session.hello` 拉历史按 `public_id`/`call_id` 合并，不覆盖在途流式与本地气泡；主 agent tool-logs 补工具卡
- pending 补写 `enqueue_on_fail=False`，失败累加 retries，满 5 次丢弃
- worker ToolLog 写入真实 decision；blocked/timeout/rejected 同样落库
- hydrate 按滑动窗口裁剪内存历史（库内全文仍供 REST 展示）；`model_id` 解析/解密失败标 unresolved，报 MODEL_ERROR，不静默 heuristic
- 删除会话先 `stop_session_subagents`；跨 loop 重建引擎时 dispose 旧 engine

## 全量审查修复 (2026-08-28，M3 后)
- 跨会话 shell 误杀：进程组 key 改 `session:agent`（原裸 agent_id="main" 跨会话共享，stop/删会话会 kill 其他会话主 agent 在途命令）
- 窗口切分吸附 tool 组边界：`window_slice` 起点落在 tool 结果上时向前扩展至所属 assistant(tool_calls)，防窗口切开配对导致模型 API 400；loop/_build_messages、subagent 快照、hydrate 统一复用
- 流式重试防重复：`astream_with_retry` 仅在未产出任何 chunk 时重试，中途失败直接抛错（原重试全文与已广播部分内容拼接重复）
- `agent.stop` 兜底不再误杀主 agent：目标不存在/已结束时回 error 事件（原 stale worker id 停止会兜底终止主 agent）
- gather 异常合成 `[异常]` 工具结果而非丢弃（loop+subagent），保证 assistant.tool_calls 与 tool 消息一一配对
- 迟到结果续聊喂回（PLAN §2.4 缺口补齐）：迁移 `0002_late_fed_back` + `load_late_subagent_results/mark_subagent_fed_back`，hydrate 注入 `[迟到子 agent 结果 ...]` 并落库，仅喂一次
- 前端：tool.start 按 subagent_id 过滤（worker 工具卡不再泄漏主聊天）、mergeChatMessages 按 user 内容去重 local 气泡（重连不重复）、subMessageOwner 用后清理、WS 默认改同源 `/ws`（VITE_WS_URL 兼容带/不带 /ws）
- 杂项：空/`__raw` shell 命令不执行标错误（防空命令静默成功）；`session.delete` 广播到被删会话；`message.send` 校验 content 类型；删除 `_dispatch_worker_tools` 中不可达的 finish_worker 分支；派生计数改按唤醒周期重置 + 先原子占用失败回滚；`manager.drop` 清理会话子 agent 注册表/批次与会话放行规则；去除 assistant 行同轮重复落库
- 未改（有意保留）：worker ToolLog `message_id` 为 NULL——子 agent 对话不落 Message（落库会污染 hydrate 主上下文）；`MessageOut` schema 预留
- 验证：`ruff` 0 告警 / `pytest 53 passed`（+5 回归：窗口吸附/迟到喂回/进程组隔离/注册表清理/空命令拒绝）/ `tsc` / `eslint` / `vite build` 通过；迁移 0002 已执行

## 待办 M4
- Redis 全面：agent 状态 TTL、pending 审批、会话放行规则、断线 hello 对账、摘要缓存增量合并
