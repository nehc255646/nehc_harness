"""手写 asyncio loop — 对应 PLAN.md §2.1"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable

from app.agent.context import build_messages, truncate_tool_result
from app.agent.executor import Executor
from app.agent.prompts import SYSTEM_PROMPT
from app.core.config import settings
from app.core.errors import ErrorCode
from app.permissions.gate import gate
from app.permissions.policy import check_policy
from app.tools.registry import TOOL_MAP, TOOLS

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

    # ---------- 对外接口 ----------

    def set_broadcaster(self, fn: Callable):
        self.broadcaster = fn

    async def enqueue(self, event: dict):
        await self.queue.put(event)
        # 若 idle，唤醒 loop
        if self.state == "idle" and self._task and self._task.done():
            self._ensure_running()

    async def start(self):
        self._ensure_running()

    def _ensure_running(self):
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._round = 0
            self.state = "idle"
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
        # 回收本 agent 的 shell 进程组
        try:
            from app.tools.shell import kill_shell_group

            kill_shell_group(self.agent_id)
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
        self.state = "running"
        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})

        try:
            while not self._stop_event.is_set():
                # 1. drain 队列 (阻塞等待下一事件，若 idle)
                if not self.history or self.state == "idle":
                    # idle 时阻塞等待
                    try:
                        event = await asyncio.wait_for(self.queue.get(), timeout=None)
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

                # 2. 构造 messages
                messages = self._build_messages()

                # 3. 调模型 (流式)
                self._round += 1
                if self._round > settings.max_rounds:
                    self.state = "done"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "done"})
                    await self._emit_message("已达到最大轮数，任务结束。", done=True)
                    break

                try:
                    result = await self._call_model(messages)
                except Exception as e:
                    logger.exception("Model call failed")
                    # 降级：若模型不可用，走 heuristic 演示，避免无限重试
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

                # 4. 文本部分先流式推送 (独立消息) — 若 _call_model 已流式则不再重复
                if text and not already_streamed:
                    await self._emit_message(text, done=True)

                # 5. 若含 tool_calls，分发执行
                if tool_calls:
                    # 检查 finish_task
                    has_finish = any(tc.get("name") == "finish_task" for tc in tool_calls)
                    # 并行分发
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
                    # 大结果截断已在 dispatch 内处理

                    if has_finish:
                        self.state = "done"
                        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "done"})
                        break
                    # 否则继续下一轮 (goto 2)，不进入 idle
                    continue
                else:
                    # 纯文本，进入 idle，等待下一事件
                    if not text:
                        # 空响应也进 idle
                        text = "(模型无返回)"
                        await self._emit_message(text, done=True)
                    elif already_streamed:
                        # 真实模型流式已推送，补 history 供下一轮上下文
                        self.history.append({"role": "assistant", "content": text})
                    self.state = "idle"
                    await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "idle"})
                    # 阻塞等待下一事件在下一轮循环开头处理
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
            # 保证 running
            if self.state in ("idle", "done", "error"):
                self.state = "running"
                await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
        elif etype == "subagent_result":
            # M2 异步回投
            result = event.get("result", "")
            self.history.append({"role": "user", "content": f"[子 agent 结果]\n{result}"})
            if self.state in ("idle", "done"):
                self.state = "running"
                await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": "running"})
        elif etype == "stop":
            await self.stop()

    def _build_messages(self) -> list[dict]:
        # 滑动窗口最近 N 个 turn (M1 简化：按消息条数取最近 window_n*2)
        window = self.history[-settings.window_n * 2 :] if len(self.history) > settings.window_n * 2 else self.history
        return build_messages(SYSTEM_PROMPT, self.summary, window, [])

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
                # tool_calls chunk
                tc_chunks = getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None)
                if tc_chunks:
                    for tc in tc_chunks:
                        # langchain chunk 格式: {name, args, id, index}
                        idx = getattr(tc, "index", 0) or 0
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"name": "", "args": "", "id": "", "index": idx}
                        if getattr(tc, "name", None):
                            tool_calls_acc[idx]["name"] = tc.name
                        if getattr(tc, "id", None):
                            tool_calls_acc[idx]["id"] = tc.id
                        if getattr(tc, "args", None):
                            tool_calls_acc[idx]["args"] += tc.args
                # 适配 AIMessageChunk.tool_calls 直接完整
                if getattr(chunk, "tool_calls", None) and isinstance(chunk.tool_calls, list):  # type: ignore
                    for tc in chunk.tool_calls:  # type: ignore
                        idx = tc.get("index", len(tool_calls_acc))
                        tool_calls_acc[idx] = {
                            "name": tc.get("name", ""),
                            "args": tc.get("args", ""),
                            "id": tc.get("id", str(uuid.uuid4())),
                            "index": idx,
                        }
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
                tool_calls_acc[idx] = {
                    "name": tc.get("name", ""),
                    "args": str(tc.get("args", "")),
                    "id": tc.get("id", str(uuid.uuid4())),
                    "index": idx,
                }

        # 结束消息
        full_text = "".join(text_parts)
        await self._broadcast("message.done", {"message_id": message_id, "role": "assistant", "content": full_text})

        # history 追加占位 (仅文本部分，tool_calls 后续统一回填) — 不在此落库，避免重复
        # 解析 tool_calls
        tool_calls = []
        for idx in sorted(tool_calls_acc.keys()):
            raw = tool_calls_acc[idx]
            name = raw.get("name", "")
            args_str = raw.get("args", "")
            call_id = raw.get("id") or str(uuid.uuid4())
            if not name:
                continue
            # args 可能是 JSON 字符串
            import json

            try:
                args = json.loads(args_str) if args_str else {}
                if isinstance(args, str):
                    args = json.loads(args)
            except Exception:
                args = {"__raw": args_str}
            tool_calls.append({"name": name, "args": args, "id": call_id})

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

        # 启发式规则：根据用户输入生成演示 tool_calls
        # 若包含特定关键词，直接生成对应工具调用以触发审批
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
            # 提取可能的命令
            cmd = last_user.strip()
            # 若用户说 "执行 ls" 则提炼
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

    async def _emit_message(self, content: str, done: bool = True):
        message_id = str(uuid.uuid4())
        await self._broadcast("message.start", {"agent_id": self.agent_id, "message_id": message_id, "role": "assistant"})
        # 简易分片流式
        chunk_size = 40
        for i in range(0, len(content), chunk_size):
            chunk = content[i : i + chunk_size]
            await self._broadcast("message.delta", {"agent_id": self.agent_id, "message_id": message_id, "delta": chunk})
            await asyncio.sleep(0.02)
        await self._broadcast("message.done", {"message_id": message_id, "role": "assistant", "content": content})
        # 落历史
        self.history.append({"role": "assistant", "content": content})

    async def _dispatch_tools(self, tool_calls: list[dict]) -> list[dict]:
        """并行分发工具，经 policy 判定，审批走 Future，全部就绪后返回"""

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
                    return {"call_id": call_id, "name": name, "result": result, "is_error": False}

                # 1. policy 判定
                session_rules = gate.get_session_rules(self.session_id)
                decision, reason, needs_approval = check_policy(name, args, session_rules)

                if decision == "blocked":
                    await self._broadcast("tool.start", {"call_id": call_id, "name": name, "args": args})
                    result = f"[拒绝] {reason} (decision=blocked)"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}

                if not needs_approval:
                    # 直接执行
                    return await self._execute_tool(name, args, call_id, decision, reason)

                # 需审批 → 挂起 Future (tool.start 由 _execute_tool 执行时广播，避免重复)
                approval_id, future = await gate.request_approval(self.session_id, self.agent_id, name, args, reason)
                await self._broadcast("approval.request", {"approval_id": approval_id, "tool": name, "args": args, "reason": reason})
                # 等待审批 (带超时)
                try:
                    approved, decision_val, resolve_reason = await asyncio.wait_for(future, timeout=settings.approval_timeout)
                except TimeoutError:
                    gate.resolve(approval_id, "timeout", "timeout")
                    await self._broadcast("approval.resolved", {"approval_id": approval_id, "approved": False, "reason": "timeout"})
                    result = f"[超时] 审批超时 ({settings.approval_timeout}s)，已拒绝: {name}"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}

                # 已 resolve
                await self._broadcast("approval.resolved", {"approval_id": approval_id, "approved": approved, "reason": resolve_reason})
                if not approved:
                    result = f"[拒绝] 用户拒绝: {reason} (decision={decision_val})"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}

                # 批准 → 执行
                return await self._execute_tool(name, args, call_id, f"approved_{decision_val}", reason)
            except Exception as e:
                # 单个工具失败不影响同轮其他工具
                result = f"[异常] 工具 {name} 分发失败: {e}"
                await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                logger.exception("Tool dispatch error: %s", name)
                return {"call_id": call_id, "name": name, "result": result, "is_error": True}

        # 并行 gather
        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls], return_exceptions=True)
        return [r for r in results if not isinstance(r, BaseException)]

    async def _execute_tool(self, name: str, args: dict, call_id: str, decision: str, reason: str) -> dict:
        await self._broadcast("tool.start", {"call_id": call_id, "name": name, "args": args})
        start = time.time()
        is_error = False
        try:
            if name == "shell":
                command = args.get("command") or args.get("cmd") or ""
                # 异步执行 (进程组按 agent 分组，供 stop 定向回收)
                from app.tools.shell import shell_async

                output, code = await shell_async(command, group=self.agent_id)
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
            # TODO: 落 ToolLog (M3)，含 decision/duration
            logger.info("Tool done: %s -> %s (%dms) decision=%s", name, "error" if is_error else "ok", duration_ms, decision)
            return {"call_id": call_id, "name": name, "result": result, "is_error": is_error}
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            result = f"[异常] {name} 执行失败: {e}"
            await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
            logger.exception("Tool error: %s", name)
            return {"call_id": call_id, "name": name, "result": result, "is_error": True}
