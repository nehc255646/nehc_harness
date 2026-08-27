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

## 待办 M2
- spawn_subagent/spawn_worker 快照/隔离/回投
