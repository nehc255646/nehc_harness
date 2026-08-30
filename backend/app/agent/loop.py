"""手写 asyncio loop"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable

from app import persist as persist_mod
from app.agent.context import (
    accumulate_tool_calls,
    build_messages,
    estimate_tokens,
    next_summary_cache,
    normalize_tool_args,
    parse_tool_calls,
    shell_command,
    should_summarize,
    slid_fingerprint,
    truncate_tool_result,
    unmatched_tool_results,
    window_slice,
)
from app.agent.executor import Executor
from app.agent.prompts import PLAN_SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.agent.stream import ThinkTagSplitter, iter_channels
from app.core import rtstore
from app.core.config import settings
from app.core.errors import ErrorCode
from app.permissions.gate import gate
from app.permissions.policy import check_policy
from app.tools.registry import SPAWN_TOOLS, TOOL_MAP, normalize_work_mode, tools_for_work_mode

# M2 子 agent 依赖延迟导入，避免循环


logger = logging.getLogger("harness.loop")


def _msg_sig(m: dict) -> str:
    """消息内容签名，用于摘要合并去重。"""
    return json.dumps(m, ensure_ascii=False, sort_keys=True, default=str)


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
        self.summary_version: int = 0
        self.summary_covered: int = 0
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._round = 0
        self.executor = Executor()
        # 当前轮 pending 的消息 id，用于流式
        self._current_message_id: str | None = None
        # 当前轮已派生工作型数量（防全量转包，跨多次 spawn 调用累计）
        self._turn_spawned: int = 0
        # 摘要合并失败后的重试冷却（避免每轮空转调 LLM）
        self._summary_retry_after: float = 0.0
        self.work_mode: str = "auto"

    # ---------- 对外接口 ----------

    def set_broadcaster(self, fn: Callable):
        self.broadcaster = fn

    def set_work_mode(self, mode: str | None) -> None:
        self.work_mode = normalize_work_mode(mode)

    def _system_prompt(self) -> str:
        return PLAN_SYSTEM_PROMPT if self.work_mode == "plan" else SYSTEM_PROMPT

    def _bound_tools(self):
        return tools_for_work_mode(self.work_mode)

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
        try:
            cache = await rtstore.get_summary_cache(self.session_id)
        except Exception:
            cache = None
        if cache:
            self.summary_version = int(cache.get("version") or 0)
            self.summary_covered = int(cache.get("covered_count") or 0)
            if cache.get("text"):
                self.summary = str(cache["text"])
        if summary and not self.summary:
            self.summary = summary
        pending_slid = list((cache or {}).get("pending_slid") or [])
        if hist or pending_slid:
            hist = list(hist or [])
            if pending_slid:
                hist_sigs = {_msg_sig(m) for m in hist}
                hist = [m for m in pending_slid if _msg_sig(m) not in hist_sigs] + hist
            has_summary = bool(self.summary)
            if has_summary and not pending_slid:
                _slid, window = window_slice(hist, settings.window_n)
                self.history = window
            else:
                self.history = hist
            for extra in unmatched_tool_results(self.history):
                self.history.append(extra)
                await self._persist_message(
                    "tool",
                    extra["content"],
                    tool_call_id=extra.get("tool_call_id"),
                    name=extra.get("name"),
                )
            logger.info("Hydrated %d messages for session=%s", len(self.history), self.session_id)
        if self.summary and not cache:
            try:
                await rtstore.set_summary_cache(
                    self.session_id,
                    next_summary_cache(None, self.summary, 0, ""),
                )
            except Exception:
                logger.debug("warm summary cache failed", exc_info=True)
        # PLAN §2.4 时序兜底：主 agent done 期间完成的迟到结果，续聊时喂回
        try:
            late_runs = await persist_mod.load_late_subagent_results(self.session_id)
        except Exception:
            late_runs = []
        for run in late_runs:
            content = f"[迟到子 agent 结果 {run.subagent_id}]\n{run.result}"
            self.history.append({"role": "user", "content": content})
            try:
                pid = await persist_mod.save_message(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    role="user",
                    content=content,
                    enqueue_on_fail=False,
                )
            except Exception:
                logger.debug("persist late subagent result failed", exc_info=True)
                pid = None
            if pid:
                await persist_mod.mark_subagent_fed_back(run.subagent_id)
        if late_runs:
            logger.info("Fed back %d late subagent results for session=%s", len(late_runs), self.session_id)
        self.executor = await Executor.from_session_id(self.session_id)
        try:
            row = await persist_mod.get_session(self.session_id)
            if row is not None:
                self.set_work_mode(getattr(row, "work_mode", None))
        except Exception:
            logger.debug("hydrate work_mode failed", exc_info=True)

    def _ensure_running(self):
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._round = 0
            self.state = "idle"
            self._turn_spawned = 0
            rtstore.fire_and_forget(rtstore.set_agent_state(self.session_id, self.agent_id, "idle"))
            self._task = asyncio.create_task(self.run())
            logger.info("AgentLoop task started: session=%s", self.session_id)

    async def stop(self):
        try:
            from app.agent.subagent import stop_session_subagents

            await stop_session_subagents(self.session_id)
        except Exception:
            logger.exception("stop subagents from main failed: %s", self.session_id)
        self._stop_event.set()
        self._shutdown_cleanup()
        await self._set_state("done")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _shutdown_cleanup(self):
        """停止前置清理：清队列 / 拒审批 / 回收 shell 进程组（可从任务内部安全调用）"""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        gate.reject_all_for_session(self.session_id, reason="stopped")
        try:
            from app.tools.shell import kill_shell_group

            kill_shell_group(self._shell_group())
        except Exception as e:
            logger.debug("Kill shell group failed: %s", e)

    def _shell_group(self) -> str:
        # 进程组 key 必须会话内唯一：不同会话的主 agent 同名 "main"，裸 agent_id 会互相误杀
        return f"{self.session_id}:{self.agent_id}"

    # ---------- 广播 ----------

    async def _set_state(self, state: str) -> None:
        self.state = state
        try:
            await rtstore.set_agent_state(self.session_id, self.agent_id, state)
        except Exception:
            logger.debug("rtstore set_agent_state failed", exc_info=True)
        await self._broadcast("agent.state", {"agent_id": self.agent_id, "state": state})

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
        await self._set_state("idle" if self.queue.empty() else "running")

        try:
            while not self._stop_event.is_set():
                try:
                    await persist_mod.flush_pending()
                except Exception:
                    logger.debug("flush_pending failed", exc_info=True)
                # 1. drain 队列：空闲则阻塞等下一事件；当前节点（一轮模型+工具）结束后再把积压的
                # 用户消息 / 子 agent 回投写进历史，供下一轮模型看到（不打断本轮）
                if not self.history or self.state == "idle":
                    try:
                        event = await self.queue.get()
                        if self._stop_event.is_set():
                            break
                        await self._handle_incoming(event)
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        logger.exception("Queue handling failed")
                        continue
                await self._drain_pending()

                # 队列内 stop 事件：清理已完成，直接退出主循环
                if self._stop_event.is_set():
                    await self._set_state("done")
                    break

                await self._maybe_compact()
                # 2. 构造 messages
                messages = self._build_messages()

                # 3. 调模型 (流式)
                self._round += 1
                if self._round > settings.max_rounds:
                    await self._set_state("done")
                    await self._emit_message("已达到最大轮数，任务结束。", done=True)
                    break

                if getattr(self.executor, "unresolved", False):
                    await self._broadcast(
                        "error",
                        {"code": ErrorCode.MODEL_ERROR, "message": "会话模型无法解析或解密"},
                    )
                    await self._set_state("error")
                    try:
                        event = await self.queue.get()
                        await self._handle_incoming(event)
                        await self._set_state("running")
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
                        await self._set_state("error")
                        try:
                            event = await self.queue.get()
                            await self._handle_incoming(event)
                            await self._set_state("running")
                        except asyncio.CancelledError:
                            break
                        continue
                    try:
                        result = self._heuristic_fallback(messages)
                        result["streamed"] = False
                        logger.info("Fallback to heuristic after model error")
                    except Exception:
                        await self._broadcast("error", {"code": ErrorCode.MODEL_ERROR, "message": str(e)})
                        await self._set_state("error")
                        # 等待新消息唤醒，避免空转
                        try:
                            event = await self.queue.get()
                            await self._handle_incoming(event)
                            await self._set_state("running")
                        except asyncio.CancelledError:
                            break
                        continue

                tool_calls = result.get("tool_calls") or []
                text = result.get("text") or ""
                already_streamed = result.get("streamed", False)
                thinking = result.get("thinking") or ""

                # 4. 纯文本分支：流式过的仅补 history，未流式的推送并记录
                if not tool_calls:
                    if not text:
                        # 空响应兜底
                        text = "(模型无返回)"
                        await self._emit_message(text, done=True)
                    elif already_streamed:
                        # 真实模型流式已推送，补 history 供下一轮上下文
                        self.history.append({"role": "assistant", "content": text})
                        await self._persist_message(
                            "assistant", text, public_id=self._current_message_id, thinking=thinking
                        )
                    else:
                        await self._emit_message(text, done=True)
                    # 进入 idle，等待下一事件
                    await self._set_state("idle")
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
                    thinking=thinking,
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
                    fin = next((tc for tc in tool_calls if tc.get("name") == "finish_task"), None)
                    fin_args = normalize_tool_args((fin or {}).get("args") or {})
                    fin_msg = str(fin_args.get("message") or "").strip()
                    if fin_msg and fin_msg != (text or "").strip():
                        await self._emit_message(fin_msg, done=True, record=True)
                    await self._set_state("done")
                    break
                # 否则继续下一轮 (goto 2)，不进入 idle
                continue

        except asyncio.CancelledError:
            logger.info("AgentLoop cancelled: %s", self.session_id)
        except Exception as e:
            logger.exception("AgentLoop crashed")
            await self._set_state("error")
            await self._broadcast("error", {"code": ErrorCode.INTERNAL, "message": str(e)})

    async def _drain_pending(self) -> None:
        """非阻塞取出积压事件，写入历史。遇 stop 后不再处理后续。"""
        drained: list[dict] = []
        while not self.queue.empty():
            try:
                drained.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for ev in drained:
            if self._stop_event.is_set():
                break
            await self._handle_incoming(ev)

    async def _handle_incoming(self, event: dict):
        etype = event.get("type")
        if etype == "user_message":
            content = event.get("content", "")
            self.history.append({"role": "user", "content": content})
            await self._persist_message("user", content)
            try:
                new_title = await persist_mod.maybe_autotitle(self.session_id, content)
                if new_title:
                    await self._broadcast("session.update", {"title": new_title, "session_id": self.session_id})
            except Exception:
                logger.debug("autotitle failed", exc_info=True)
            # PLAN §2.4：派生上限按唤醒周期（turn）计
            self._turn_spawned = 0
            # 保证 running
            if self.state in ("idle", "done", "error"):
                await self._set_state("running")
        elif etype == "subagent_result":
            # M2 交互型异步回投
            result = event.get("result", "")
            sid = event.get("subagent_id", "")
            content = f"[子 agent 结果 {sid}]\n{result}"
            self.history.append({"role": "user", "content": content})
            await self._persist_message("user", content)
            self._turn_spawned = 0
            if self.state in ("idle", "done", "error"):
                await self._set_state("running")
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
                await self._set_state("running")
        elif etype == "stop":
            # 队列内停止：只做清理与置位，由 run 主循环退出（不从任务内部自取消）
            self._shutdown_cleanup()
            self._stop_event.set()

    def _build_messages(self) -> list[dict]:
        # 滑动窗口最近 N 个 turn（window_slice 吸附 tool 组边界）
        _slid, window = window_slice(self.history, settings.window_n)
        return build_messages(self._system_prompt(), self.summary, window, [])

    async def _persist_message(
        self,
        role: str,
        content: str,
        public_id: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list | None = None,
        name: str | None = None,
        thinking: str | None = None,
    ) -> bool:
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
                thinking=thinking,
            )
            return True
        except Exception:
            logger.debug("persist message failed", exc_info=True)
            return False

    async def _persist_tool_log(
        self,
        name: str,
        args: dict,
        result: str | dict,
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
        """token 超阈值：旧摘要 + 滑出消息增量合并；失败则不截断 history，冷却后重试。"""
        tokens = estimate_tokens(self.history, self.summary)
        ctx = getattr(self.executor, "context_window", None) or 128000
        if not should_summarize(tokens, ctx, settings.summary_token_ratio):
            return
        # 摘要失败冷却期内不重试，避免每轮空转调 LLM
        if time.time() < self._summary_retry_after:
            return
        slid, window = window_slice(self.history, settings.window_n)
        if not slid:
            return
        try:
            cache = await rtstore.get_summary_cache(self.session_id)
        except Exception:
            cache = None
        pending_slid = list((cache or {}).get("pending_slid") or [])
        # 摘要失败不截断 history 后重试时，滑出集是 pending 的超集，按内容去重防重复合并
        slid_sigs = {_msg_sig(m) for m in slid}
        pending_slid = [m for m in pending_slid if _msg_sig(m) not in slid_sigs]
        to_merge = pending_slid + slid
        fp = slid_fingerprint(to_merge)
        if cache and cache.get("last_slid_hash") == fp and cache.get("text") and not pending_slid:
            self.summary = str(cache["text"])
            self.summary_version = int(cache.get("version") or 0)
            self.summary_covered = int(cache.get("covered_count") or 0)
            self.history = window
            return
        new_sum = await self._merge_summary(to_merge)
        payload = next_summary_cache(cache, new_sum, len(to_merge), fp, pending_slid=None if new_sum else to_merge)
        if new_sum:
            self.summary = new_sum
            self.summary_version = int(payload["version"])
            self.summary_covered = int(payload["covered_count"])
            self.history = window
        else:
            # 摘要失败：不截断 history，防止内容既不在窗口也不在摘要；滑出消息仍缓存待合并
            self._summary_retry_after = time.time() + 120
            logger.warning(
                "摘要失败，保留完整 history（%d 条，滑出 %d 条待合并）session=%s",
                len(self.history),
                len(to_merge),
                self.session_id,
            )
        try:
            await persist_mod.save_summary(self.session_id, self.summary)
        except Exception:
            logger.debug("save_summary failed", exc_info=True)
        try:
            await rtstore.set_summary_cache(self.session_id, payload)
        except Exception:
            logger.debug("set_summary_cache failed", exc_info=True)

    async def _merge_summary(self, slid: list[dict]) -> str | None:
        if self.executor._llm is None:
            return None
        text = "\n".join(f"{m.get('role')}: {str(m.get('content', ''))[:800]}" for m in slid)
        prompt = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Previous summary:\n{self.summary or '(none)'}\n\nSlid-out messages:\n{text}",
            },
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
        if self.executor._llm is None or getattr(self.executor, "demo", False):
            # 重置消息 id：heuristic 路径不产生流式 message，防止 tool_calls 分支复用上一轮 id 覆盖旧落库行
            self._current_message_id = None
            res = self._heuristic_fallback(messages)
            res["streamed"] = False
            return res

        message_id = str(uuid.uuid4())
        self._current_message_id = message_id
        await self._broadcast("message.start", {"agent_id": self.agent_id, "message_id": message_id, "role": "assistant"})

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}
        pushed_output = False

        try:
            async for thinking, delta, tcs in iter_channels(self.executor, messages, self._bound_tools()):
                if thinking:
                    thinking_parts.append(thinking)
                    pushed_output = True
                    await self._broadcast(
                        "message.delta",
                        {
                            "agent_id": self.agent_id,
                            "message_id": message_id,
                            "delta": thinking,
                            "channel": "thinking",
                        },
                    )
                if delta:
                    text_parts.append(delta)
                    pushed_output = True
                    await self._broadcast(
                        "message.delta",
                        {"agent_id": self.agent_id, "message_id": message_id, "delta": delta},
                    )
                if tcs:
                    accumulate_tool_calls(tool_calls_acc, {"tool_call_chunks": tcs})
        except Exception as e:
            # 已向客户端推过 delta 则不再 ainvoke 重播，避免 UI 正文重复
            if pushed_output:
                logger.warning("Stream failed after output, keep partial: %s", e)
            else:
                logger.warning("Stream failed, fallback to ainvoke: %s", e)
                result = await self.executor.ainvoke(messages, self._bound_tools())
                raw = getattr(result, "content", "") or ""
                extra = getattr(result, "additional_kwargs", None) or {}
                think_raw = ""
                if isinstance(extra, dict):
                    think_raw = extra.get("reasoning_content") or extra.get("reasoning") or ""
                    if not isinstance(think_raw, str):
                        think_raw = ""
                splitter = ThinkTagSplitter()
                tag_th, tag_ct = splitter.feed(raw if isinstance(raw, str) else "")
                th_f, ct_f = splitter.flush()
                think_all = think_raw + tag_th + th_f
                text = tag_ct + ct_f
                if not text and isinstance(raw, str) and not tag_th:
                    text = raw
                if think_all:
                    thinking_parts = [think_all]
                    await self._broadcast(
                        "message.delta",
                        {
                            "agent_id": self.agent_id,
                            "message_id": message_id,
                            "delta": think_all,
                            "channel": "thinking",
                        },
                    )
                if text:
                    text_parts = [text]
                    await self._broadcast(
                        "message.delta",
                        {"agent_id": self.agent_id, "message_id": message_id, "delta": text},
                    )
                raw_tcs = getattr(result, "tool_calls", None) or []
                for idx, tc in enumerate(raw_tcs):
                    tool_calls_acc[idx] = {
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}) if isinstance(tc.get("args", {}), dict) else str(tc.get("args", "")),
                        "id": tc.get("id", str(uuid.uuid4())),
                        "index": idx,
                    }

        full_text = "".join(text_parts)
        full_thinking = "".join(thinking_parts)
        await self._broadcast(
            "message.done",
            {"message_id": message_id, "role": "assistant", "content": full_text, "thinking": full_thinking},
        )

        # history 追加占位 (仅文本部分，tool_calls 后续统一回填) — 不在此落库，避免重复
        tool_calls = parse_tool_calls(tool_calls_acc)
        return {"text": full_text, "tool_calls": tool_calls, "streamed": True, "thinking": full_thinking}

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

        if self.work_mode == "plan":
            if "read" in last_user_lower or "读取" in last_user_lower or "glob" in last_user_lower or "grep" in last_user_lower or "列出" in last_user_lower:
                return {
                    "text": "plan 模式：先只读查看工作区。",
                    "tool_calls": [{"name": "glob", "args": {"pattern": "**/*"}, "id": str(uuid.uuid4())}],
                }
            return {
                "text": (
                    f"当前为 plan 模式（只读）。针对「{last_user[:80]}」不会改文件或执行命令。"
                    "请先用只读工具调研，或切到底栏 Auto 后再执行。"
                ),
                "tool_calls": [],
            }

        # 启发式规则：根据用户输入生成演示 tool_calls（派生优先，避免被 shell 吞）
        if "spawn_subagent" in last_user_lower or "派生交互" in last_user_lower or (
            "子 agent" in last_user_lower and "spawn" not in last_user_lower and "工作" not in last_user_lower
        ):
            return {
                "text": "交互型子 agent 请从顶栏「子 Agent」打开，不会由主对话自动派生。",
                "tool_calls": [],
            }
        if "spawn_workers" in last_user_lower:
            return {
                "text": "准备派生两个互不重叠的演示工人：",
                "tool_calls": [{
                    "name": "spawn_workers",
                    "args": {"tasks": ["列出 workspace 顶层文件名", "读取 hello.txt（若存在）"]},
                    "id": str(uuid.uuid4()),
                }],
            }
        if "spawn_worker" in last_user_lower or "派生工作" in last_user_lower or "后台任务" in last_user_lower:
            return {
                "text": "准备派生一个窄范围演示工人：",
                "tool_calls": [{
                    "name": "spawn_worker",
                    "args": {"task": "列出 workspace 顶层文件名"},
                    "id": str(uuid.uuid4()),
                }],
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
        """按序分发工具，经 policy 判定，审批走 Future（同类放行可作用于本轮后续调用）"""

        async def _run_one(tc: dict) -> dict:
            call_id = tc.get("id") or ""
            name = tc.get("name") or ""
            args = normalize_tool_args(tc.get("args") or {})
            tc["args"] = args
            try:
                if name == "shell" and not shell_command(args):
                    result = "[错误] shell 命令为空或参数解析失败"
                    await self._broadcast("tool.start", {
                        "call_id": call_id, "name": name, "args": args,
                        "message_id": self._current_message_id,
                    })
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                    await self._persist_tool_log(name, args, result, call_id, True, 0, "blocked", "empty_command")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}

                # finish_task 直接处理
                if name == "finish_task":
                    await self._broadcast("tool.start", {
                        "call_id": call_id, "name": name, "args": args,
                        "message_id": self._current_message_id,
                    })
                    result = f"[完成] {args.get('message', '任务完成')}"
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": False})
                    await self._persist_tool_log(name, args, result, call_id, False, 0, "config_allow")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": False}

                # M2 spawn_* 直接处理（不走 policy）；plan 模式禁止派生
                if name in SPAWN_TOOLS:
                    await self._broadcast("tool.start", {
                        "call_id": call_id, "name": name, "args": args,
                        "message_id": self._current_message_id,
                    })
                    if self.work_mode == "plan":
                        result = f"[拒绝] plan 模式只读，禁止 {name} (decision=blocked)"
                        await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
                        await self._persist_tool_log(name, args, result, call_id, True, 0, "blocked", result)
                        return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                    result = await self._handle_spawn_tool(name, args)
                    # spawn 结果是否需要截断
                    result = truncate_tool_result(str(result), settings.max_tool_result_tokens)
                    is_err = result.startswith(("[拒绝]", "[错误]"))
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": is_err})
                    await self._persist_tool_log(name, args, result, call_id, is_err, 0, "config_allow")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": is_err}

                # 1. policy 判定
                session_rules = gate.get_session_rules(self.session_id)
                decision, reason, needs_approval = check_policy(
                    name, args, session_rules, work_mode=self.work_mode
                )

                if decision == "blocked":
                    await self._broadcast("tool.start", {
                        "call_id": call_id, "name": name, "args": args,
                        "message_id": self._current_message_id,
                    })
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
                    await self._set_state("awaiting_approval")
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
                        await self._set_state("running")

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

        def _synth_stopped(tc: dict) -> dict:
            return {
                "call_id": tc.get("id") or "",
                "name": tc.get("name") or "",
                "result": "[中断] 任务已停止，工具未执行完毕",
                "is_error": True,
            }

        # 按序执行：同轮「同类均执行」才能落到后续 shell；也避免审批卡片叠成一排
        out: list[dict] = []
        try:
            for tc in tool_calls:
                if self._stop_event.is_set():
                    syn = _synth_stopped(tc)
                    await self._persist_tool_log(
                        syn["name"], tc.get("args") or {}, syn["result"], syn["call_id"], True, 0, "stopped", "stopped"
                    )
                    out.append(syn)
                    continue
                try:
                    out.append(await _run_one(tc))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("Tool dispatch error: %s", tc.get("name"))
                    out.append(
                        {
                            "call_id": tc.get("id") or "",
                            "name": tc.get("name") or "",
                            "result": f"[异常] 工具 {tc.get('name')} 分发失败: {e}",
                            "is_error": True,
                        }
                    )
            return out
        except asyncio.CancelledError:
            done_ids = {r.get("call_id") for r in out}
            for r in out:
                await self._persist_message(
                    "tool", r["result"], tool_call_id=r.get("call_id"), name=r.get("name")
                )
            for tc in tool_calls:
                cid = tc.get("id") or ""
                if cid in done_ids:
                    continue
                syn = _synth_stopped(tc)
                await self._persist_message("tool", syn["result"], tool_call_id=cid, name=syn["name"])
                await self._persist_tool_log(
                    syn["name"], tc.get("args") or {}, syn["result"], cid, True, 0, "stopped", "stopped"
                )
            raise

    async def _handle_spawn_tool(self, name: str, args: dict) -> str:
        # 延迟导入避免循环
        try:
            from app.agent.manager import manager
            from app.agent.subagent import spawn_worker_batch
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
            return "[拒绝] 交互型子 agent 需由用户从顶栏打开，主 agent 不能派生"
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
        args = normalize_tool_args(args)
        await self._broadcast("tool.start", {
            "call_id": call_id, "name": name, "args": args,
            "message_id": self._current_message_id,
        })
        start = time.time()
        is_error = False
        diff = None
        try:
            if name == "shell":
                command = shell_command(args)
                # 空/不可解析参数（如 __raw 兜底）不执行，防止空命令静默成功
                if not command:
                    result = "[错误] shell 命令为空或参数解析失败"
                    is_error = True
                    duration_ms = int((time.time() - start) * 1000)
                    await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": is_error})
                    await self._persist_tool_log(name, args, result, call_id, is_error, duration_ms, decision, reason)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": is_error}

                from app.tools.shell import shell_async

                async def _on_progress(tail: str):
                    await self._broadcast("tool.progress", {"call_id": call_id, "tail": tail})

                output, code = await shell_async(command, group=self._shell_group(), on_progress=_on_progress)
                result = output
                is_error = code != 0 and code != 124  # 124 为超时
            elif name == "write":
                from app.tools.files import apply_write

                result, extra = apply_write(str(args.get("path") or ""), str(args.get("content") or ""))
                diff = (extra or {}).get("diff")
                is_error = str(result).startswith("[错误]")
            elif name == "edit":
                from app.tools.files import apply_edit

                result, extra = apply_edit(
                    str(args.get("path") or ""),
                    str(args.get("old_string") or ""),
                    str(args.get("new_string") or ""),
                )
                diff = (extra or {}).get("diff")
                is_error = str(result).startswith("[错误]")
            else:
                tool_obj = TOOL_MAP.get(name)
                if not tool_obj:
                    result = f"[错误] 未知工具: {name}"
                    is_error = True
                else:
                    try:
                        result = await tool_obj.ainvoke(args)  # type: ignore
                    except Exception:
                        result = tool_obj.invoke(args)  # type: ignore
                    result = str(result)
                    is_error = str(result).startswith("[错误]")

            full_text = str(result)
            injected = truncate_tool_result(full_text, settings.max_tool_result_tokens)
            duration_ms = int((time.time() - start) * 1000)
            stored: dict = {"text": full_text}
            if diff:
                stored["diff"] = diff
            payload = {"call_id": call_id, "result": injected, "is_error": is_error}
            if diff:
                payload["diff"] = diff
            await self._broadcast("tool.result", payload)
            await self._persist_tool_log(name, args, stored, call_id, is_error, duration_ms, decision, reason)
            logger.info("Tool done: %s -> %s (%dms) decision=%s", name, "error" if is_error else "ok", duration_ms, decision)
            return {"call_id": call_id, "name": name, "result": injected, "is_error": is_error}
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            result = f"[异常] {name} 执行失败: {e}"
            await self._broadcast("tool.result", {"call_id": call_id, "result": result, "is_error": True})
            await self._persist_tool_log(name, args, result, call_id, True, duration_ms, decision, reason)
            logger.exception("Tool error: %s", name)
            return {"call_id": call_id, "name": name, "result": result, "is_error": True}
