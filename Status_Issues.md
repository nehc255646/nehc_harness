# 状态与问题记录

## M1 — 核心闭环 (已完成 2026-08-27)
- 脚手架 + core/config/redis/logging/errors 完成
- executor: ChatOpenAI + bind_tools + 流式 + 重试 + heuristic fallback (无 key 时演示)
- loop: 手写 asyncio loop 完整实现 (drain/原子回填/文本与 tool_calls 并存/max轮数/done-error-idle/awaiting_approval展示态)
- tools: read/write/edit/glob/grep (@tool) + shell (async 进程组 killpg) + registry + finish_task
- permissions: rules(allow_rules.yaml 2-token前缀/黑名单拆分)/policy(四路分流)/gate(per-agent Future+超时+session规则共享)
- api/ws: 完整 WS 协议 (session.hello/pending对账/session_allow_rules/心跳/广播/单次消费/多会话)
- 前端: ws.ts/store + Chat/ToolCallCard/ApprovalModal(三按钮)/useAgentStream + 暗黑+青色主题
- 验证: WS 脚本验证 三选审批→同类放行(approve_similar)→黑名单blocked→只读直接放行→流式渲染 均通过

## 已知限制 (按 PLAN)
- 模型未配置时走 heuristic 演示，真实 LLM 需配置 OPENAI_API_KEY/BASE_URL (或 M3 DB)
- 持久化/Redis全面/子agent 待 M2-M4
- 进程崩溃丢在途状态为已知限制 (单进程 --workers 1)

## M1 审查修复 (2026-08-27)
- A1 loop 纯文本回复补 history：真实模型流式已推送时追加 `history`，多轮上下文不再丢失
- A2 审批路径重复 `tool.start` 去除：审批通过仅由 `_execute_tool` 广播一次，前端不再重复卡片
- A3 同轮 `gather(return_exceptions=True)` + `_run_one` 兜底，单工具异常不崩整轮
- A4 `gate.resolve` 对 `wait_for` 超时已 cancel 的 Future 清理残留，不再泄漏 `_pending`
- B1 shell 进程组按 `agent_id` 分组：`_active_pgs: dict[str,set]` + `kill_shell_group(group)`，`stop` 定向回收
- B2 `files._resolve` 越权直接拒绝 (`None`) + `glob` 拒绝 `../`/绝对路径，读写/编辑/搜索均返回越权错误
- B3 `Executor.astream_with_retry` 指数退避，`loop._call_model` 改用之
- B4 WS 心跳发 `ping`→等 `pong`，连续 3 次超时判死关闭；前端 `ws.ts` 收到 `ping` 自动回 `pong`
- B6 `config` 移除冗余 `model_post_init` (pydantic-settings 已映射)、修正 `encryption_key` 为合法 Fernet、移 `asyncmy` 统一 `aiomysql`、`READONLY_TOOLS` 统一到 `registry`
- C1 `session.create` 将连接迁移到新 `session_id`，`broadcast` 随新会话路由；前端 `store` 的 `session.hello` 更新 `sessionId/sessions`，`SessionSidebar` 跟随
- C2 前端 `store` 增加 `sessionAllowRules/sessions`，处理 `session.update`，`sendMessage` 清空上一轮 `toolCalls`；重连/新会话按 `session_id` 是否变化决定是否清空展示
- C3 前端安装 `eslint` + `typescript-eslint` + `eslint.config.js`，`npm run lint` 通过
- D1 `ruff` 58 告警清零：`--fix` 15 个 + 手工修复 `S110/TRY401/PLW1510/S112` + `BLE001` 全局忽略（工具容错型盲捕获为刻意设计）
- D2 补测试 21 项：`test_rules` 黑名单拆分/`test_policy` 四路分流/`test_context` 截断/`test_files` 越权 + 读写往返/`test_ws` 审批三选与黑名单拦截（`TestClient`）
- 验证：`ruff`/`tsc --noEmit`/`eslint`/`pytest 21 passed` 均通过

## 待办 M2
- spawn_subagent/spawn_worker 快照/隔离/回投
