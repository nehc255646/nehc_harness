"""手写 asyncio loop — 对应 PLAN.md §2.1"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable

from app import persist as persist_mod
from app.agent.context import (
    accumulate_tool_calls,
    build_messages,
    estimate_tokens,
    parse_tool_calls,
    should_summarize,
    truncate_tool_result,
    window_slice,
)
from app.agent.executor import Executor
from app.agent.prompts import SYSTEM_PROMPT
from app.core.config import settings
from app.core.errors import ErrorCode
from app.permissions.gate import gate
from app.permissions.policy import check_policy
from app.tools.registry import TOOL_MAP, TOOLS

# M2 子 agent 依赖延迟导入，避免循环


logger = logging.getLogger("harness.loop")


class AgentLoop:
    """每轮：drain 队列 → 构造 messages → 调模型 → 分发工具(含用户门) → 原子回填"""

    def __init__(self, session_id: str, agent_id: str = "main", broadcaster: Callable | None = None):
        self.session_id = session_id
        self.agent_id = agent_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.broadcaster = broadcaster  # async def broadcast(event, payload)
        self.state: str = "idle"
        self.history: list[dict] = []  # {role, content, tool_calls?, tool_call_id?}
        self.summary: str | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._round = 0
        self.executor = Executor()
        # 当前轮 pending 的消息 id，用于流式
        self._current_message_id: str | None = None
        # 当前轮已派生工作型数量（防全量转包，跨多次 spawn 调用累计）
        self._turn_spawned: int = 0

    # ---------- 对外接口 ----------

    def set_broadcaster(self, fn: Callable):
        self.broadcaster = fn

    async def enqueue(self, event: dict):
        await self.queue.put(event)
        # 任务已结束（idle/done/error 任意终态）时，新事件唤醒新一轮 run；
        # 任务仍在运行时事件由 run 内的 queue.get 消费，不重复拉起
        if self._task is None or self._task.done():
            self._ensure_running()

    async def start(self):
        self._ensure_running()

    async def hydrate_from_db(self):
        """M3: 从 DB 历史 + 摘要重建上下文，并按会话模型实例化 executor。"""
        hist, summary, _mid = await persist_mod.load_history(self.session_id)
        if hist:
            _slid, window = window_slice(hist, settings.window_n)
            self.history = window
            logger.info("Hydrated %d messages for session=%s", len(window), self.session_id)
        if summary:
            self.summary = summary
        # PLAN §2.4 时序兜底：主 agent done 期间完成的迟到结果，续聊时喂回
        try:
            late_runs = await persist_mod.load_late_subagent_results(self.session_id)
        except Exception:
            late_runs = []
        for run in late_runs:
            content = f"[迟到子 agent 结果 {run.subagent_id}]\n{run.result}"
            self.history.append({"role": "user", "content": content})
            await self._persist_message("user", content)
            await persist_mod.mark_subagent_fed_back(run.subagent_id)
        if late_runs:
            logger.info("Fed back %d late subagent results for session=%s", len(late_runs), self.session_id)
        self.executor = await Executor.from_session_id(self.session_id)

    def _ensure_running(self):
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._round = 0
            self.state = "idle"
            self._turn_spawned = 0
            self._task = asyncio.create_task(self.run())
            logger.info("AgentLoop task started: session=%s", self.session_id)

    async def stop(self):
        self._stop_event.set()
        # 清空队列
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # 拒绝所有 pending 审批
        gate.reject_all_for_session(self.session_id, reason="stopped")
        # 回收本 agent 的 shell 进程组（key 含 session 前缀，避免跨会话误杀）
        try:
            from app.tools.shell import kill_shell_group

            kill_shell_group(self._shell_group())
        except Exception as e:
            logger.debug("Kill shell group failed: %s", e)
        self.state = "done"
        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "done"})
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _shell_group(self) -> str:
        # 进程组 key 必须会话内唯一：不同会话的主 agent 同名 "main"，裸 agent_id 会互相误杀
        return f"{self.session_id}:{self.agent_id}"

    # ---------- 广播 ----------

    async def _broadcast(self, event: str, payload: dict):
        if self.broadcaster:
            try:
                await self.broadcaster(self.session_id, event, payload)
            except Exception as e:
                logger.warning("Broadcast failed %s: %s", event, e)
        else:
            # 尝试全局 ws broadcast
            try:
                from app.api.ws import broadcast

                await broadcast(self.session_id, event, payload)
            except Exception as e:
                logger.debug("Fallback broadcast failed: %s", e)

    # ---------- 主循环 ----------

    async def run(self):
        logger.info("AgentLoop run started: session=%s", self.session_id)
        # 队列空则 idle：hydrate 重建的历史不是进行中的一轮，须等新的用户/回投事件
        self.state = "idle" if self.queue.empty() else "running"
        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": self.state})

        try:
            while not self._stop_event.is_set():
                try:
                    await persist_mod.flush_pending()
                except Exception:
                    logger.debug("flush_pending failed", exc_info=True)
                # 1. drain 队列 (阻塞等待下一事件，若 idle)
                if not self.history or self.state == "idle":
                    # idle 时阻塞等待
                    try:
                        event = await self.queue.get()
                        # 若收到 stop 信号，直接退出
                        if self._stop_event.is_set():
                            break
                        await self._handle_incoming(event)
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        logger.exception("Queue handling failed")
                        continue
                else:
                    # 非 idle：drain 所有积压事件 (非阻塞)
                    drained = []
                    while not self.queue.empty():
                        try:
                            drained.append(self.queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    for ev in drained:
                        await self._handle_incoming(ev)

                await self._maybe_compact()
                # 2. 构造 messages
                messages = self._build_messages()

                # 3. 调模型 (流式)
                self._round += 1
                if self._round > settings.max_rounds:
                    self.state = "done"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "done"})
                    await self._emit_message("已达到最大轮数，任务结束。", done=True)
                    break

                if getattr(self.executor, "unresolved", False):
                    await self._broadcast(
                        "error",
                        {"code": ErrorCode.MODEL_ERROR, "message": "会话模型无法解析或解密"},
                    )
                    self.state = "error"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "error"})
                    try:
                        event = await self.queue.get()
                        await self._handle_incoming(event)
                        self.state = "running"
                        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
                    except asyncio.CancelledError:
                        break
                    continue

                try:
                    result = await self._call_model(messages)
                except Exception as e:
                    logger.exception("Model call failed")
                    # 已选模型解析失败不走演示；其余模型错误才 heuristic
                    if getattr(self.executor, "unresolved", False):
                        await self._broadcast("error", {"code": ErrorCode.MODEL_ERROR, "message": str(e)})
                        self.state = "error"
                        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "error"})
                        try:
                            event = await self.queue.get()
                            await self._handle_incoming(event)
                            self.state = "running"
                            await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
                        except asyncio.CancelledError:
                            break
                        continue
                    try:
                        result = self._heuristic_fallback(messages)
                        result["streamed"] = False
                        logger.info("Fallback to heuristic after model error")
                    except Exception:
                        await self._broadcast("error", {"code": ErrorCode.MODEL_ERROR, "message": str(e)})
                        self.state = "error"
                        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "error"})
                        # 等待新消息唤醒，避免空转
                        try:
                            event = await self.queue.get()
                            await self._handle_incoming(event)
                            self.state = "running"
                            await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
                        except asyncio.CancelledError:
                            break
                        continue

                tool_calls = result.get("tool_calls") or []
                text = result.get("text") or ""
                already_streamed = result.get("streamed", False)

                # 4. 纯文本分支：流式过的仅补 history，未流式的推送并记录
                if not tool_calls:
                    if not text:
                        # 空响应兜底
                        text = "(模型无返回)"
                        await self._emit_message(text, done=True)
                    elif already_streamed:
                        # 真实模型流式已推送，补 history 供下一轮上下文
                        self.history.append({"role": "assistant", "content": text})
                        await self._persist_message("assistant", text, public_id=self._current_message_id)
                    else:
                        await self._emit_message(text, done=True)
                    # 进入 idle，等待下一事件
                    self.state = "idle"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "idle"})
                    continue

                # 5. tool_calls 分支：文本只随 assistant_msg 入 history，避免重复记录
                if text and not already_streamed:
                    await self._emit_message(text, done=True, record=False)
                # 检查 finish_task
                has_finish = any(tc.get("name") == "finish_task" for tc in tool_calls)
                # 并行分发
                if not self._current_message_id:
                    self._current_message_id = str(uuid.uuid4())
                # 先落 assistant 行，供同轮 ToolLog.message_id 关联
                await self._persist_message(
                    "assistant",
                    text or "",
                    public_id=self._current_message_id,
                    tool_calls=tool_calls,
                )
                tool_results = await self._dispatch_tools(tool_calls)
                # 原子回填历史 (assistant tool_calls + tool results)
                assistant_msg = {
                    "role": "assistant",
                    "content": text or "",
                    "tool_calls": tool_calls,
                }
                self.history.append(assistant_msg)
                for tr in tool_results:
                    self.history.append(
                        {
                            "role": "tool",
                            "content": tr["result"],
                            "tool_call_id": tr["call_id"],
                            "name": tr["name"],
                        }
                    )
                    await self._persist_message(
                        "tool",
                        tr["result"],
                        tool_call_id=tr["call_id"],
                        name=tr["name"],
                    )
                # 大结果截断已在 dispatch 内处理

                if has_finish:
                    self.state = "done"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "done"})
                    break
                # 否则继续下一轮 (goto 2)，不进入 idle
                continue

        except asyncio.CancelledError:
            logger.info("AgentLoop cancelled: %s", self.session_id)
        except Exception as e:
            logger.exception("AgentLoop crashed")
            self.state = "error"
            await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "error"})
            await self._broadcast("error", {"code": ErrorCode.INTERNAL, "message": str(e)})

    async def _handle_incoming(self, event: dict):
        etype = event.get("type")
        if etype == "user_message":
            content = event.get("content", "")
            self.history.append({"role": "user", "content": content})
            await self._persist_message("user", content)
            # PLAN §2.4：派生上限按唤醒周期（turn）计
            self._turn_spawned = 0
            # 保证 running
            if self.state in ("idle", "done", "error"):
                self.state = "running"
                await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
        elif etype == "subagent_result":
            # M2 交互型异步回投
            result = event.get("result", "")
            sid = event.get("subagent_id", "")
            content = f"[子 agent 结果 {sid}]\n{result}"
            self.history.append({"role": "user", "content": content})
            await self._persist_message("user", content)
            self._turn_spawned = 0
            if self.state in ("idle", "done", "error"):
                self.state = "running"
                await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
        elif etype == "worker_batch_done":
            # M2 工作型批量聚合回投
            payload = event.get("payload") or {}
            batch_id = payload.get("batch_id", "")
            workers = payload.get("workers") or []
            lines = []
            for w in workers:
                lines.append(f"- {w.get('subagent_id')} [{w.get('status')}]: {w.get('result','')[:500]}")
            content = f"[工作型批量完成] batch_id={batch_id}\n" + "\n".join(lines)
            self.history.append({"role": "user", "content": content})
            await self._persist_message("user", content)
            self._turn_spawned = 0
            # 聚合事件也广播，供前端 worker.batch_done 已由子 agent 广播，此处仅注入上下文
            if self.state in ("idle", "done", "error"):
                self.state = "running"
                await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
        elif etype == "stop":
            await self.stop()

    def _build_messages(self) -> list[dict]:
        # 滑动窗口最近 N 个 turn（window_slice 吸附 tool 组边界）
        _slid, window = window_slice(self.history, settings.window_n)
        return build_messages(SYSTEM_PROMPT, self.summary, window, [])

    async def _persist_message(
        self,
        role: str,
        content: str,
        public_id: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list | None = None,
        name: str | None = None,
    ):
        try:
            await persist_mod.save_message(
                session_id=self.session_id,
                agent_id=self.agent_id,
                role=role,
                content=content,
                public_id=public_id,
                tool_call_id=tool_call_id,
                tool_calls=tool_calls,
                name=name,
            )
        except Exception:
            logger.debug("persist message failed", exc_info=True)

    async def _persist_tool_log(
        self,
        name: str,
        args: dict,
        result: str,
        call_id: str,
        is_error: bool,
        duration_ms: int | None,
        decision: str | None,
        rule_hit: str | None = None,
    ):
        try:
            await persist_mod.save_tool_log(
                session_id=self.session_id,
                agent_id=self.agent_id,
                name=name,
                args=args or {},
                result=result,
                tool_call_id=call_id,
                is_error=is_error,
                duration_ms=duration_ms,
                decision=decision,
                rule_hit=rule_hit,
                message_public_id=self._current_message_id,
            )
        except Exception:
            logger.debug("persist tool_log failed", exc_info=True)

    async def _maybe_compact(self):
        """token 超阈值：旧摘要 + 滑出消息合并；失败则丢窗口外并告警。"""
        tokens = estimate_tokens(self.history, self.summary)
        ctx = getattr(self.executor, "context_window", None) or 128000
        if not should_summarize(tokens, ctx, settings.summary_token_ratio):
            return
        slid, window = window_slice(self.history, settings.window_n)
        if not slid:
            return
        new_sum = await self._merge_summary(slid)
        if new_sum:
            self.summary = new_sum
        else:
            logger.warning("摘要失败，丢弃窗口外 %d 条 session=%s", len(slid), self.session_id)
        self.history = window
        try:
            await persist_mod.save_summary(self.session_id, self.summary)
        except Exception:
            logger.debug("save_summary failed", exc_info=True)

    async def _merge_summary(self, slid: list[dict]) -> str | None:
        if self.executor._llm is None:
            return None
        text = "\n".join(f"{m.get('role')}: {str(m.get('content', ''))[:800]}" for m in slid)
        prompt = [
            {"role": "system", "content": "将对话压缩为简洁摘要，保留任务目标、关键决策、文件变更与未决事项。只输出摘要正文。"},
            {"role": "user", "content": f"旧摘要:\n{self.summary or '(无)'}\n\n新滑出消息:\n{text}"},
        ]
        try:
            result = await self.executor.ainvoke(prompt)
            content = getattr(result, "content", None) or str(result)
            return str(content) if content else None
        except Exception as e:
            logger.warning("摘要生成失败: %s", e)
            return None

    async def _call_model(self, messages: list[dict]) -> dict:
        """调用模型，支持流式，返回 {text, tool_calls, streamed}"""
        # 若 executor 未初始化或为演示 key，走 heuristic fallback (无 key 时保证 M1 可验收)
        if self.executor._llm is None or getattr(self.executor, "api_key", "") in ("sk-test", "", None):
            res = self._heuristic_fallback(messages)
            res["streamed"] = False
            return res

        message_id = str(uuid.uuid4())
        self._current_message_id = message_id
        await self._broadcast("message.start", {"agent_id": self.agent_id, "message_id": message_id, "role": "assistant"})

        text_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}

        try:
            async for chunk in self.executor.astream_with_retry(messages, TOOLS):
                # chunk 可能是 AIMessageChunk
                delta = getattr(chunk, "content", "") or ""
                if delta:
                    text_parts.append(delta)
                    await self._broadcast("message.delta", {"agent_id": self.agent_id, "message_id": message_id, "delta": delta})
                accumulate_tool_calls(tool_calls_acc, chunk)
        except Exception as e:
            # 若流式失败，尝试非流式
            logger.warning("Stream failed, fallback to ainvoke: %s", e)
            result = await self.executor.ainvoke(messages, TOOLS)
            text = getattr(result, "content", "") or ""
            if text:
                text_parts = [text]
                await self._broadcast("message.delta", {"agent_id": self.agent_id, "message_id": message_id, "delta": text})
            raw_tcs = getattr(result, "tool_calls", None) or []
            for idx, tc in enumerate(raw_tcs):
                # 非流式返回的 args 已是 dict，直接保留
                tool_calls_acc[idx] = {
                    "name": tc.get("name", ""),
                    "args": tc.get("args", {}) if isinstance(tc.get("args", {}), dict) else str(tc.get("args", "")),
                    "id": tc.get("id", str(uuid.uuid4())),
                    "index": idx,
                }

        # 结束消息
        full_text = "".join(text_parts)
        await self._broadcast("message.done", {"message_id": message_id, "role": "assistant", "content": full_text})

        # history 追加占位 (仅文本部分，tool_calls 后续统一回填) — 不在此落库，避免重复
        tool_calls = parse_tool_calls(tool_calls_acc)
        return {"text": full_text, "tool_calls": tool_calls, "streamed": True}

    def _heuristic_fallback(self, messages: list[dict]) -> dict:
        """无 LLM 时的启发式 fallback，保证 M1 演示可用 — 仅对最新 user 消息生成一次工具调用"""
        # 若最后一条消息不是 user，说明已处理过该 user 的工具调用，直接返回完成避免无限循环
        if messages and messages[-1].get("role") != "user":
            # 检查自最后一条 user 后是否已有 tool 结果，若有则结束
            has_user = any(m.get("role") == "user" for m in messages)
            if has_user:
                last_user_content = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        last_user_content = m.get("content", "")
                        break
                return {
                    "text": f"已完成对 \"{last_user_content[:50]}\" 的处理。",
                    "tool_calls": [],
                }
            return {"text": "已完成。", "tool_calls": []}

        # 取最后一条 user 消息
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        last_user_lower = last_user.lower()

        # 回投聚合消息不触发新派生，避免无限递归
        if last_user.startswith(("[工作型批量完成]", "[子 agent 结果")):
            return {"text": f"已收到批量回投：{last_user[:200]}", "tool_calls": []}
        if last_user.startswith(("[交互型", "[迟到")):
            return {"text": "已知交互结果", "tool_calls": []}

        # 启发式规则：根据用户输入生成演示 tool_calls（派生优先，避免被 shell 吞）
        if "spawn_subagent" in last_user_lower or "派生交互" in last_user_lower:
            return {
                "text": "准备派生交互型子 agent：",
                "tool_calls": [{"name": "spawn_subagent", "args": {"behavior_desc": "与用户确认需求", "goal": last_user[:200]}, "id": str(uuid.uuid4())}],
            }
        if "spawn_workers" in last_user_lower:
            return {
                "text": "准备批量派生后台工作型：",
                "tool_calls": [{"name": "spawn_workers", "args": {"tasks": [f"子任务1: {last_user[:50]}", f"子任务2: {last_user[50:100]}"]}, "id": str(uuid.uuid4())}],
            }
        if "spawn_worker" in last_user_lower or "派生工作" in last_user_lower or "后台任务" in last_user_lower:
            return {
                "text": "准备派生后台工作型：",
                "tool_calls": [{"name": "spawn_worker", "args": {"task": last_user[:200]}, "id": str(uuid.uuid4())}],
            }
        # 子 agent 通用关键词需在 spawn_workers 之后，避免批量被单条吞
        if "子 agent" in last_user_lower and "spawn" not in last_user_lower:
            return {
                "text": "准备派生交互型子 agent：",
                "tool_calls": [{"name": "spawn_subagent", "args": {"behavior_desc": "与用户确认需求", "goal": last_user[:200]}, "id": str(uuid.uuid4())}],
            }
        if "批量" in last_user_lower:
            return {
                "text": "准备批量派生后台工作型：",
                "tool_calls": [{"name": "spawn_workers", "args": {"tasks": [f"子任务1: {last_user[:50]}", f"子任务2: {last_user[50:100]}"]}, "id": str(uuid.uuid4())}],
            }
        if "rm -rf" in last_user_lower or "mkfs" in last_user_lower:
            cmd = last_user.strip()
            for prefix in ["执行", "运行", "shell", "执行命令"]:
                if cmd.startswith(prefix):
                    cmd = cmd[len(prefix) :].strip(" :：")
                    break
            return {
                "text": "检测到危险命令，尝试执行：",
                "tool_calls": [{"name": "shell", "args": {"command": cmd}, "id": str(uuid.uuid4())}],
            }
        if "shell" in last_user_lower or "执行" in last_user_lower or "ls" in last_user_lower or "echo" in last_user_lower:
            cmd = last_user.strip()
            for prefix in ["执行", "运行", "shell", "执行命令"]:
                if cmd.startswith(prefix):
                    cmd = cmd[len(prefix) :].strip(" :：")
                    break
            if not cmd:
                cmd = "ls -la"
            return {
                "text": f"准备执行 shell 命令：{cmd}",
                "tool_calls": [{"name": "shell", "args": {"command": cmd}, "id": str(uuid.uuid4())}],
            }
        if "read" in last_user_lower or "读取" in last_user_lower or "glob" in last_user_lower or "grep" in last_user_lower:
            return {
                "text": "准备读取文件：",
                "tool_calls": [{"name": "glob", "args": {"pattern": "**/*"}, "id": str(uuid.uuid4())}],
            }
        if "write" in last_user_lower or "写入" in last_user_lower or "创建" in last_user_lower:
            return {
                "text": "准备写入文件：",
                "tool_calls": [{"name": "write", "args": {"path": "hello.txt", "content": "hello from harness"}, "id": str(uuid.uuid4())}],
            }
        # 默认返回文本，引导用户尝试工具
        return {
            "text": f"收到: {last_user}\n\n(当前未配置模型 API Key，处于 heuristic 演示模式。试试发送 \"执行 ls -la\" 或 \"执行 echo hello\" 来触发审批流程，或配置 OPENAI_API_KEY 后获得真实模型能力。)",
            "tool_calls": [],
        }

    async def _emit_message(self, content: str, done: bool = True, record: bool = True):
        message_id = str(uuid.uuid4())
        await self._broadcast("message.start", {"agent_id": self.agent_id, "message_id": message_id, "role": "assistant"})
        # 简易分片流式
        chunk_size = 40
        for i in range(0, len(content), chunk_size):
            chunk = content[i : i + chunk_size]
            await self._broadcast("message.delta", {"agent_id": self.agent_id, "message_id": message_id, "delta": chunk})
            await asyncio.sleep(0.02)
        await self._broadcast("message.done", {"message_id": message_id, "role": "assistant", "content": content})
        self._current_message_id = message_id
        # 落历史 (record=False 时由调用方随 assistant_msg 统一回填，避免重复)
        if record:
            self.history.append({"role": "assistant", "content": content})
            await self._persist_message("assistant", content, public_id=message_id)

    async def _dispatch_tools(self, tool_calls: list[dict]) -> list[dict]:
        """并行分发工具，经 policy 判定，审批走 Future，全部就绪后返回（M2 支持 spawn_*）"""

        async def _run_one(tc: dict) -> dict:
            call_id = tc.get("id") or ""
            name = tc.get("name") or ""
            args = tc.get("args") or {}
            try:
                # finish_task 直接处理
                if name == "finish_task":
                    await self._broadcast("tool.start", {"call_id": call_id, "name": name, "args": args})
                    result = f"[完成] {args.get('message', '任务完成')}"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": False})
                    await self._persist_tool_log(name, args, result, call_id, False, 0, "config_allow")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": False}

                # M2 spawn_* 直接处理（不走 policy）
                if name in ("spawn_subagent", "spawn_worker", "spawn_workers"):
                    await self._broadcast("tool.start", {"call_id": call_id, "name": name, "args": args})
                    result = await self._handle_spawn_tool(name, args)
                    # spawn 结果是否需要截断
                    result = truncate_tool_result(str(result), settings.max_tool_result_tokens)
                    is_err = result.startswith(("[拒绝]", "[错误]"))
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": is_err})
                    await self._persist_tool_log(name, args, result, call_id, is_err, 0, "config_allow")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": is_err}

                # 1. policy 判定
                session_rules = gate.get_session_rules(self.session_id)
                decision, reason, needs_approval = check_policy(name, args, session_rules)

                if decision == "blocked":
                    await self._broadcast("tool.start", {"call_id": call_id, "name": name, "args": args})
                    result = f"[拒绝] {reason} (decision=blocked)"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    await self._persist_tool_log(name, args, result, call_id, True, 0, "blocked", reason)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}

                if not needs_approval:
                    # 直接执行
                    return await self._execute_tool(name, args, call_id, decision, reason)

                # 需审批 → 挂起 Future (tool.start 由 _execute_tool 执行时广播，避免重复)
                approval_id, future = await gate.request_approval(self.session_id, self.agent_id, name, args, reason)
                await self._broadcast("approval.request", {"approval_id": approval_id, "tool": name, "args": args, "reason": reason})
                # 展示态：进入等待审批（PLAN §2.5）
                if self.state != "awaiting_approval":
                    self.state = "awaiting_approval"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "awaiting_approval"})
                # 等待审批 (带超时)
                try:
                    approved, decision_val, resolve_reason = await asyncio.wait_for(future, timeout=settings.approval_timeout)
                except TimeoutError:
                    gate.resolve(approval_id, "timeout", "timeout")
                    await self._broadcast("approval.resolved", {"approval_id": approval_id, "approved": False, "reason": "timeout"})
                    result = f"[超时] 审批超时 ({settings.approval_timeout}s)，已拒绝: {name}"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    await self._persist_tool_log(name, args, result, call_id, True, 0, "timeout")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                finally:
                    # 审批结束：本 agent 无其余 pending 时才回运行态（并行多审批场景）；
                    # stop 取消路径不回运行态，避免覆盖 done
                    still_pending = any(p.agent_id == self.agent_id for p in gate.list_pending(self.session_id))
                    if not self._stop_event.is_set() and self.state == "awaiting_approval" and not still_pending:
                        self.state = "running"
                        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})

                # 已 resolve
                await self._broadcast("approval.resolved", {"approval_id": approval_id, "approved": approved, "reason": resolve_reason})
                if not approved:
                    result = f"[拒绝] 用户拒绝: {reason} (decision={decision_val})"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    await self._persist_tool_log(name, args, result, call_id, True, 0, "rejected", reason)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}

                # 批准 → 执行
                mapped = "approved_similar" if decision_val == "approve_similar" else "approved_once"
                return await self._execute_tool(name, args, call_id, mapped, reason)
            except Exception as e:
                # 单个工具失败不影响同轮其他工具
                result = f"[异常] 工具 {name} 分发失败: {e}"
                await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                logger.exception("Tool dispatch error: %s", name)
                return {"call_id": call_id, "name": name, "result": result, "is_error": True}

        # 并行 gather；异常合成错误结果而非丢弃，保证 history 中
        # assistant.tool_calls 与 tool 消息一一配对（缺失会导致模型 API 400）
        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls], return_exceptions=True)
        out: list[dict] = []
        for tc, r in zip(tool_calls, results):
            if isinstance(r, BaseException):
                out.append(
                    {
                        "call_id": tc.get("id") or "",
                        "name": tc.get("name") or "",
                        "result": f"[异常] 工具 {tc.get('name')} 分发失败: {r}",
                        "is_error": True,
                    }
                )
            else:
                out.append(r)
        return out

    async def _handle_spawn_tool(self, name: str, args: dict) -> str:
        # 延迟导入避免循环
        try:
            from app.agent.manager import manager
            from app.agent.subagent import spawn_interactive, spawn_worker_batch
        except Exception as e:
            return f"[错误] 子 agent 模块未就绪: {e}"
        # 获取主 agent 唤醒入口与 broadcaster（传 enqueue 而非裸 queue，保证终态后回投可唤醒）
        broadcaster = self.broadcaster
        # 防全量转包：按轮累计派生数（检查+累加间无 await，事件循环内原子）
        if name == "spawn_worker":
            requested = 1
        elif name == "spawn_workers":
            tasks = args.get("tasks") or []
            requested = len(tasks)
        else:
            requested = 0
        if requested and self._turn_spawned + requested > settings.max_workers_per_turn:
            return (
                f"[拒绝] 单轮派生工作型总数不超过 {settings.max_workers_per_turn}，"
                f"本轮已派生 {self._turn_spawned}，再请求 {requested}"
            )
        if name == "spawn_subagent":
            behavior_desc = args.get("behavior_desc") or args.get("behavior") or ""
            goal = args.get("goal") or args.get("task") or ""
            if not behavior_desc and not goal:
                return "[错误] spawn_subagent 需提供 behavior_desc 或 goal"
            return await spawn_interactive(
                self.session_id, behavior_desc, goal, self.history, self.summary, broadcaster, self.enqueue, manager.get
            )
        elif name == "spawn_worker":
            task = args.get("task") or ""
            constraints = args.get("constraints") or ""
            if not task:
                return "[错误] spawn_worker 需提供 task"
            # 检查+占用原子（无 await），并行 spawn 不超限；失败回滚
            self._turn_spawned += requested
            result = await spawn_worker_batch(
                self.session_id, [task], self.history, self.summary, broadcaster, self.enqueue, manager.get, constraints
            )
            if result.startswith(("[拒绝]", "[错误]")):
                self._turn_spawned -= requested
            return result
        elif name == "spawn_workers":
            tasks = args.get("tasks") or []
            constraints = args.get("constraints") or ""
            if not tasks:
                return "[错误] spawn_workers 需提供 tasks 数组"
            self._turn_spawned += requested
            result = await spawn_worker_batch(
                self.session_id, tasks, self.history, self.summary, broadcaster, self.enqueue, manager.get, constraints
            )
            if result.startswith(("[拒绝]", "[错误]")):
                self._turn_spawned -= requested
            return result
        return f"[错误] 未知 spawn 工具: {name}"

    async def _execute_tool(self, name: str, args: dict, call_id: str, decision: str, reason: str) -> dict:
        await self._broadcast("tool.start", {"call_id": call_id, "name": name, "args": args})
        start = time.time()
        is_error = False
        try:
            if name == "shell":
                command = args.get("command") or args.get("cmd") or ""
                # 空/不可解析参数（如 __raw 兜底）不执行，防止空命令静默成功
                if not str(command).strip():
                    result = "[错误] shell 命令为空或参数解析失败"
                    is_error = True
                    duration_ms = int((time.time() - start) * 1000)
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": is_error})
                    await self._persist_tool_log(name, args, result, call_id, is_error, duration_ms, decision, reason)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": is_error}
                # 异步执行 (进程组按 session:agent 分组，供 stop 定向回收)
                from app.tools.shell import shell_async

                output, code = await shell_async(command, group=self._shell_group())
                result = output
                is_error = code != 0 and code != 124  # 124 为超时
            else:
                tool_obj = TOOL_MAP.get(name)
                if not tool_obj:
                    result = f"[错误] 未知工具: {name}"
                    is_error = True
                else:
                    # LangChain tool 的 ainvoke
                    try:
                        result = await tool_obj.ainvoke(args)  # type: ignore
                    except Exception:
                        result = tool_obj.invoke(args)  # type: ignore
                    result = str(result)
                    # 大结果截断注入上下文，完整落 ToolLog (M3)
                    result = truncate_tool_result(result, settings.max_tool_result_tokens)

            duration_ms = int((time.time() - start) * 1000)
            await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": is_error})
            await self._persist_tool_log(name, args, result, call_id, is_error, duration_ms, decision, reason)
            logger.info("Tool done: %s -> %s (%dms) decision=%s", name, "error" if is_error else "ok", duration_ms, decision)
            return {"call_id": call_id, "name": name, "result": result, "is_error": is_error}
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            result = f"[异常] {name} 执行失败: {e}"
            await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
            await self._persist_tool_log(name, args, result, call_id, True, duration_ms, decision, reason)
            logger.exception("Tool error: %s", name)
            return {"call_id": call_id, "name": name, "result": result, "is_error": True}
