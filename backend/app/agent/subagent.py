"""子 agent 快照/隔离/回投 — 分交互型与工作型 (M2 实现)"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.agent.context import (
    accumulate_tool_calls,
    build_messages,
    normalize_tool_args,
    parse_tool_calls,
    shell_command,
    truncate_tool_result,
    window_slice,
)
from app.agent.executor import Executor
from app.agent.prompts import INTERACTIVE_SYSTEM_PROMPT, WORKER_SYSTEM_PROMPT
from app.core.config import settings
from app.permissions.gate import gate
from app.permissions.policy import check_policy
from app.tools.registry import TOOLS

logger = logging.getLogger("harness.subagent")

# 交互型空闲超时：等待用户侧栏输入的最长时间，超时自动收敛（不空转调模型）
_INTERACTIVE_IDLE_TIMEOUT = 120

# LangChain 工具集：交互型仅 finish_subagent，工作型全量
try:
    from langchain_core.tools import tool
except Exception:  # pragma: no cover
    tool = lambda f: f  # type: ignore


@tool
def finish_subagent(summary: str = "") -> str:
    """交互型收敛：结束对话并将摘要回投主 agent。参数: summary"""
    return f"[交互完成] {summary}"


@tool
def finish_worker(result: str = "") -> str:
    """工作型收敛：结束后台任务并回投结果。参数: result"""
    return f"[工作完成] {result}"


# 供 Executor 绑定的工具列表
INTERACTIVE_TOOLS = [finish_subagent]
WORKER_TOOLS = TOOLS + [finish_worker]  # 与主 agent 同等 + finish_worker
WORKER_TOOL_MAP = {t.name: t for t in WORKER_TOOLS}


@dataclass
class SubAgentRecord:
    subagent_id: str
    session_id: str
    kind: str  # interactive | worker
    status: str  # running | done | error
    task: str
    behavior_desc: str = ""
    batch_id: str | None = None
    result: str | None = None
    late: bool = False
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


# 全局注册表 (单进程)
_subagents: dict[str, SubAgentRecord] = {}
_session_index: dict[str, set[str]] = {}
_batches: dict[str, dict] = {}  # batch_id -> {workers:[id], results:{id: (status,result)}, total}
_tasks: dict[str, asyncio.Task] = {}
_loops: dict[str, SubAgentLoop] = {}


def _broadcast_fn_holder():
    # 延迟获取，避免循环导入
    try:
        from app.api.ws import broadcast as ws_broadcast

        return ws_broadcast
    except Exception:
        return None


async def _broadcast(session_id: str, event: str, payload: dict, broadcaster=None):
    if broadcaster:
        try:
            await broadcaster(session_id, event, payload)
            return
        except Exception as e:
            logger.debug("Subagent broadcast via cb failed: %s", e)
    try:
        from app.api.ws import broadcast

        await broadcast(session_id, event, payload)
    except Exception as e:
        logger.debug("Subagent broadcast failed: %s", e)


def _snapshot_history(main_history: list[dict], max_pairs: int | None = None) -> list[dict]:
    n = max_pairs or settings.window_n
    _slid, window = window_slice(main_history, n)
    return list(window)


def get_active_count(session_id: str) -> int:
    s = _session_index.get(session_id, set())
    cnt = 0
    for sid in s:
        rec = _subagents.get(sid)
        if rec and rec.status == "running":
            cnt += 1
    return cnt


def get_panels(session_id: str) -> list[dict]:
    out = []
    for sid in _session_index.get(session_id, set()):
        rec = _subagents.get(sid)
        if rec and rec.kind == "interactive":
            out.append(
                {
                    "subagent_id": rec.subagent_id,
                    "kind": rec.kind,
                    "task": rec.task,
                    "status": rec.status,
                    "result": rec.result,
                    "late": rec.late,
                }
            )
    return out


def get_workers(session_id: str) -> list[dict]:
    out = []
    for sid in _session_index.get(session_id, set()):
        rec = _subagents.get(sid)
        if rec and rec.kind == "worker":
            out.append(
                {
                    "subagent_id": rec.subagent_id,
                    "task_summary": rec.task[:80],
                    "state": rec.status,
                    "batch_id": rec.batch_id,
                    "late": rec.late,
                }
            )
    return out


def get_subagent(subagent_id: str) -> SubAgentRecord | None:
    return _subagents.get(subagent_id)


# ---------- SubAgentLoop ----------

class SubAgentLoop:
    """交互型轻量对话 / 工作型完整 loop (含审批)"""

    def __init__(
        self,
        session_id: str,
        subagent_id: str,
        kind: str,
        task: str,
        behavior_desc: str,
        snapshot: list[dict],
        summary: str | None,
        broadcaster,
        main_enqueue,
        batch_id: str | None = None,
        manager_get=None,
    ):
        self.session_id = session_id
        self.subagent_id = subagent_id
        self.kind = kind
        self.task = task
        self.behavior_desc = behavior_desc
        self.snapshot = snapshot
        self.summary = summary
        self.broadcaster = broadcaster
        # 主 agent 队列入口（bound enqueue），保证主 agent 终态后回投仍可唤醒
        self.main_enqueue = main_enqueue
        self.batch_id = batch_id
        self.manager_get = manager_get
        self.queue: asyncio.Queue = asyncio.Queue()
        self.history: list[dict] = []
        self.state = "running"
        self._round = 0
        self._stop_requested = False
        self.executor = Executor()
        self._build_initial_history()

    async def bind_session_executor(self):
        try:
            self.executor = await Executor.from_session_id(self.session_id)
        except Exception:
            logger.debug("subagent executor bind failed", exc_info=True)

    def _build_initial_history(self):
        # 隔离快照：历史 + 行为描述 + 任务
        hist = list(self.snapshot)
        # 行为描述注入为 user 消息，便于模型感知上下文
        if self.behavior_desc:
            hist.append({"role": "user", "content": f"[行为描述] {self.behavior_desc}"})
        if self.task:
            hist.append({"role": "user", "content": f"[任务] {self.task}"})
        self.history = hist

    def _build_messages(self) -> list[dict]:
        _slid, window = window_slice(self.history, settings.window_n)
        system = INTERACTIVE_SYSTEM_PROMPT if self.kind == "interactive" else WORKER_SYSTEM_PROMPT
        return build_messages(system, self.summary, window, [])

    async def enqueue_user(self, content: str):
        await self.queue.put({"type": "user_message", "content": content})

    async def run(self):
        logger.info("SubAgent run start: %s kind=%s session=%s", self.subagent_id, self.kind, self.session_id)
        try:
            from app.core import rtstore

            await rtstore.set_agent_state(self.session_id, self.subagent_id, "running")
        except Exception:
            logger.debug("subagent state redis failed", exc_info=True)
        await self.bind_session_executor()
        # 通知前端 opened 已在 spawn 时广播，此处再补一次 worker.status
        if self.kind == "worker":
            await _broadcast(self.session_id, "worker.status", {"workers": get_workers(self.session_id)}, self.broadcaster)

        try:
            # 工作型超时控制
            if self.kind == "worker":
                try:
                    await asyncio.wait_for(self._run_loop(), timeout=settings.worker_timeout)
                except TimeoutError:
                    logger.info("Worker timeout: %s", self.subagent_id)
                    await self._finish("timeout", f"[超时] 工作型 {self.subagent_id} 执行超时 ({settings.worker_timeout}s)")
                    return
            else:
                await self._run_loop()
        except asyncio.CancelledError:
            logger.info("SubAgent cancelled: %s", self.subagent_id)
            if self._stop_requested:
                await self._finish("done", "[已停止] 用户终止")
            else:
                await self._finish("error", "[取消] 子 agent 已取消")
        except Exception as e:
            logger.exception("SubAgent crashed: %s", self.subagent_id)
            await self._finish("error", f"[异常] {e}")
        finally:
            # 清理 task 索引并回收本子 agent 的 shell 进程组（取消/超时时防孤儿进程）
            _tasks.pop(self.subagent_id, None)
            try:
                from app.tools.shell import kill_shell_group

                kill_shell_group(self.subagent_id)
            except Exception as e:
                logger.debug("Cleanup subagent shell group failed: %s", e)

    async def _run_loop(self):
        while True:
            # 交互型：首轮直接对话，之后等待用户侧栏输入；
            # 空闲超时自动收敛，不空转调模型（避免无人对话时持续烧 token）
            if self.kind == "interactive" and self._round > 0:
                rec = _subagents.get(self.subagent_id)
                if rec and rec.status != "running":
                    break
                # 先 drain 已到达的用户消息
                while not self.queue.empty():
                    try:
                        ev = self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if ev.get("type") == "user_message":
                        self.history.append({"role": "user", "content": ev.get("content", "")})
                # 无新输入且末尾非 user（模型刚回复过）→ 阻塞等待用户
                if self.queue.empty() and (not self.history or self.history[-1].get("role") != "user"):
                    try:
                        ev = await asyncio.wait_for(self.queue.get(), timeout=_INTERACTIVE_IDLE_TIMEOUT)
                        if ev.get("type") == "user_message":
                            self.history.append({"role": "user", "content": ev.get("content", "")})
                    except TimeoutError:
                        await self._finish("done", "[空闲超时] 用户未继续对话，交互型子 agent 自动收敛")
                        break

            # 构造消息并调模型
            messages = self._build_messages()
            self._round += 1
            if self._round > settings.max_rounds:
                await self._finish("done", f"[触顶] 超过最大轮数 {settings.max_rounds}")
                break

            if getattr(self.executor, "unresolved", False):
                await self._finish("error", "[模型错误] 会话模型无法解析或解密")
                break

            # 调用模型
            try:
                result = await self._call_model(messages)
            except Exception as e:
                logger.exception("SubAgent model call failed")
                if getattr(self.executor, "unresolved", False):
                    await self._finish("error", f"[模型错误] {e}")
                    break
                try:
                    result = self._heuristic_fallback(messages)
                    result["streamed"] = False
                except Exception:
                    await self._finish("error", f"[模型错误] {e}")
                    break

            text = result.get("text") or ""
            tool_calls = result.get("tool_calls") or []
            already_streamed = result.get("streamed", False)

            if text and not already_streamed:
                await self._emit_text(text)
            elif already_streamed and text:
                self.history.append({"role": "assistant", "content": text})

            if tool_calls:
                # 检查收敛工具
                finish_name = "finish_subagent" if self.kind == "interactive" else "finish_worker"
                fin = next((tc for tc in tool_calls if tc.get("name") == finish_name), None)
                if fin:
                    summary = ""
                    args = fin.get("args") or {}
                    summary = args.get("summary") or args.get("result") or str(args) or text
                    await self._finish("done", summary)
                    break

                # 工作型需执行其他工具（含审批）
                if self.kind == "worker":
                    tool_results = await self._dispatch_worker_tools(tool_calls)
                    # 原子回填
                    self.history.append({"role": "assistant", "content": text or "", "tool_calls": tool_calls})
                    for tr in tool_results:
                        self.history.append({"role": "tool", "content": tr["result"], "tool_call_id": tr["call_id"], "name": tr["name"]})
                    continue
                else:
                    # 交互型不应有其他工具，忽略
                    self.history.append({"role": "assistant", "content": text or "", "tool_calls": tool_calls})
                    for tc in tool_calls:
                        self.history.append({"role": "tool", "content": f"[忽略] 交互型不支持工具 {tc.get('name')}", "tool_call_id": tc.get("id", ""), "name": tc.get("name", "")})
                    continue
            else:
                # 纯文本（text 已随 _emit_text/流式路径入 history，此处不再追加）
                if not text and self.kind == "worker":
                    # 工作型无文本无工具 -> 尝试完成
                    await self._finish("done", "(空响应，任务结束)")
                    break
                # 交互型：继续等待用户侧栏输入；工作型：自主继续下一轮
                continue

    async def _finish(self, status: str, result: str):
        rec = _subagents.get(self.subagent_id)
        if rec:
            rec.status = status if status != "timeout" else "error"
            rec.result = result
            rec.finished_at = time.time()
            # 迟到判定：主 agent 已 done
            main_agent = None
            if self.manager_get:
                try:
                    main_agent = self.manager_get(self.session_id)
                except Exception:
                    main_agent = None
            if main_agent and main_agent.state == "done":
                rec.late = True
        self.state = status if status in ("done", "error") else "done"
        try:
            from app.core import rtstore

            await rtstore.set_agent_state(self.session_id, self.subagent_id, self.state)
        except Exception:
            logger.debug("subagent finish state redis failed", exc_info=True)

        try:
            from app import persist as persist_mod

            await persist_mod.upsert_subagent_run(
                main_session_id=self.session_id,
                subagent_id=self.subagent_id,
                kind=self.kind,
                status=rec.status if rec else self.state,
                result=result,
                late=bool(rec.late) if rec else False,
                finished=True,
            )
        except Exception:
            logger.debug("persist subagent finish failed", exc_info=True)
        # 广播状态
        if self.kind == "interactive":
            await _broadcast(self.session_id, "subagent.done", {"subagent_id": self.subagent_id, "kind": self.kind, "result_summary": result[:500]}, self.broadcaster)
        else:
            await _broadcast(self.session_id, "worker.status", {"workers": get_workers(self.session_id)}, self.broadcaster)

        # 回投主 agent
        await self._enqueue_to_main_inner(result, status)

    async def _enqueue_to_main_inner(self, result: str, status: str):
        if self.kind == "interactive":
            await _enqueue_to_main_single(self.session_id, self.subagent_id, result, status, self.main_enqueue, self.broadcaster, self.manager_get)
        else:
            await self._handle_worker_finish(result, status)

    async def _call_model(self, messages: list[dict]) -> dict:
        if self.executor._llm is None or getattr(self.executor, "demo", False):
            res = self._heuristic_fallback(messages)
            res["streamed"] = False
            return res

        # 真实模型流式 — 与 heuristic 路径同协议：message.start/delta/done + subagent_id
        tool_set = INTERACTIVE_TOOLS if self.kind == "interactive" else WORKER_TOOLS
        message_id = str(uuid.uuid4())
        await _broadcast(
            self.session_id,
            "message.start",
            {"agent_id": self.subagent_id, "message_id": message_id, "role": "assistant", "subagent_id": self.subagent_id},
            self.broadcaster,
        )

        text_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}
        # 用 astream_with_retry
        async for chunk in self.executor.astream_with_retry(messages, tool_set):
            delta = getattr(chunk, "content", "") or ""
            if delta:
                text_parts.append(delta)
                await _broadcast(
                    self.session_id,
                    "message.delta",
                    {"agent_id": self.subagent_id, "message_id": message_id, "delta": delta, "subagent_id": self.subagent_id},
                    self.broadcaster,
                )
            accumulate_tool_calls(tool_calls_acc, chunk)

        full_text = "".join(text_parts)
        await _broadcast(
            self.session_id,
            "message.done",
            {"message_id": message_id, "role": "assistant", "content": full_text, "subagent_id": self.subagent_id},
            self.broadcaster,
        )
        tool_calls = parse_tool_calls(tool_calls_acc)
        return {"text": full_text, "tool_calls": tool_calls, "streamed": True}

    def _heuristic_fallback(self, messages: list[dict]) -> dict:
        # 无 key 时的演示 fallback
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        if self.kind == "interactive":
            # 交互型：若历史仅含任务，返回引导语等待用户
            if self._round == 1:
                return {"text": f"[交互型子 agent 已就绪]\n任务: {self.task}\n请在侧栏输入以对话，完成后我将调用 finish_subagent。", "tool_calls": []}
            # 若用户已发送消息，模拟 finish
            if last_user and "finish" in last_user.lower():
                return {"text": "收到，准备收敛。", "tool_calls": [{"name": "finish_subagent", "args": {"summary": f"用户确认完成: {last_user[:100]}"}, "id": str(uuid.uuid4())}]}
            return {"text": f"收到: {last_user}\n(演示模式，发送包含 'finish' 的消息以触发 finish_subagent)", "tool_calls": []}
        else:
            # 工作型：演示执行一次 shell echo
            if self._round == 1:
                return {"text": "工作型开始执行", "tool_calls": [{"name": "shell", "args": {"command": f"echo worker:{self.task[:30]} && ls -la"}, "id": str(uuid.uuid4())}]}
            # 第二轮收敛
            return {"text": "工作完成", "tool_calls": [{"name": "finish_worker", "args": {"result": f"已完成任务: {self.task}"}, "id": str(uuid.uuid4())}]}

    async def _emit_text(self, content: str):
        mid = str(uuid.uuid4())
        await _broadcast(self.session_id, "message.start", {"agent_id": self.subagent_id, "message_id": mid, "role": "assistant", "subagent_id": self.subagent_id}, self.broadcaster)
        chunk_size = 40
        for i in range(0, len(content), chunk_size):
            chunk = content[i : i + chunk_size]
            await _broadcast(self.session_id, "message.delta", {"agent_id": self.subagent_id, "message_id": mid, "delta": chunk, "subagent_id": self.subagent_id}, self.broadcaster)
            await asyncio.sleep(0.02)
        await _broadcast(self.session_id, "message.done", {"message_id": mid, "role": "assistant", "content": content, "subagent_id": self.subagent_id}, self.broadcaster)
        self.history.append({"role": "assistant", "content": content})

    async def _dispatch_worker_tools(self, tool_calls: list[dict]) -> list[dict]:
        async def _run_one(tc: dict) -> dict:
            call_id = tc.get("id") or ""
            name = tc.get("name") or ""
            args = normalize_tool_args(tc.get("args") or {})
            tc["args"] = args
            try:
                if name == "shell" and not shell_command(args):
                    result = "[错误] shell 命令为空或参数解析失败"
                    await self._persist_worker_log(name, args, result, call_id, True, "blocked", "empty_command")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                # finish_worker 已在外层 _run_loop 拦截收敛，不进入分发
                if name in ("spawn_worker", "spawn_workers", "spawn_subagent"):
                    return {"call_id": call_id, "name": name, "result": "[拒绝] 工作型不支持递归派生", "is_error": True}
                session_rules = gate.get_session_rules(self.session_id)
                decision, reason, needs_approval = check_policy(name, args, session_rules)
                if decision == "blocked":
                    result = f"[拒绝] {reason} (blocked)"
                    await self._persist_worker_log(name, args, result, call_id, True, "blocked", reason)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                if not needs_approval:
                    return await self._execute_worker_tool(name, args, call_id, decision, reason)
                # 需审批
                approval_id, fut = await gate.request_approval(self.session_id, self.subagent_id, name, args, reason)
                await _broadcast(self.session_id, "approval.request", {"approval_id": approval_id, "tool": name, "args": args, "reason": reason, "subagent_id": self.subagent_id}, self.broadcaster)
                try:
                    approved, decision_val, resolve_reason = await asyncio.wait_for(fut, timeout=settings.approval_timeout)
                except TimeoutError:
                    gate.resolve(approval_id, "timeout", "timeout")
                    await _broadcast(self.session_id, "approval.resolved", {"approval_id": approval_id, "approved": False, "reason": "timeout"}, self.broadcaster)
                    result = f"[超时] 审批超时 {name}"
                    await self._persist_worker_log(name, args, result, call_id, True, "timeout")
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                await _broadcast(self.session_id, "approval.resolved", {"approval_id": approval_id, "approved": approved, "reason": resolve_reason}, self.broadcaster)
                if not approved:
                    result = f"[拒绝] 用户拒绝: {reason}"
                    await self._persist_worker_log(name, args, result, call_id, True, "rejected", reason)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                mapped = "approved_similar" if decision_val == "approve_similar" else "approved_once"
                return await self._execute_worker_tool(name, args, call_id, mapped, reason)
            except Exception as e:
                logger.exception("Worker tool dispatch error")
                return {"call_id": call_id, "name": name, "result": f"[异常] {e}", "is_error": True}

        out: list[dict] = []
        for tc in tool_calls:
            try:
                out.append(await _run_one(tc))
            except Exception as e:
                logger.exception("Worker tool dispatch error")
                out.append(
                    {
                        "call_id": tc.get("id") or "",
                        "name": tc.get("name") or "",
                        "result": f"[异常] 工具 {tc.get('name')} 分发失败: {e}",
                        "is_error": True,
                    }
                )
        return out

    async def _persist_worker_log(
        self,
        name: str,
        args: dict,
        result: str | dict,
        call_id: str,
        is_error: bool,
        decision: str,
        rule_hit: str | None = None,
    ) -> None:
        try:
            from app import persist as persist_mod

            await persist_mod.save_tool_log(
                session_id=self.session_id,
                agent_id=self.subagent_id,
                name=name,
                args=args or {},
                result=result,
                tool_call_id=call_id,
                is_error=is_error,
                decision=decision,
                rule_hit=rule_hit,
            )
        except Exception:
            logger.debug("persist worker tool_log failed", exc_info=True)

    async def _execute_worker_tool(
        self, name: str, args: dict, call_id: str, decision: str = "config_allow", reason: str = ""
    ) -> dict:
        await _broadcast(self.session_id, "tool.start", {"call_id": call_id, "name": name, "args": args, "subagent_id": self.subagent_id}, self.broadcaster)
        is_error = False
        diff = None
        try:
            if name == "shell":
                command = shell_command(args)
                # 空/不可解析参数（如 __raw 兜底）不执行，防止空命令静默成功
                if not command:
                    result = "[错误] shell 命令为空或参数解析失败"
                    await _broadcast(self.session_id, "tool.result", {"call_id": call_id, "result": result, "is_error": True, "subagent_id": self.subagent_id}, self.broadcaster)
                    await self._persist_worker_log(name, args, result, call_id, True, decision, reason or None)
                    return {"call_id": call_id, "name": name, "result": result, "is_error": True}
                from app.tools.shell import shell_async

                async def _on_progress(tail: str):
                    await _broadcast(
                        self.session_id,
                        "tool.progress",
                        {"call_id": call_id, "tail": tail, "subagent_id": self.subagent_id},
                        self.broadcaster,
                    )

                output, code = await shell_async(command, group=self.subagent_id, on_progress=_on_progress)
                result = output
                is_error = code != 0 and code != 124
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
                tool_obj = WORKER_TOOL_MAP.get(name)
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
            stored: dict | str = {"text": full_text}
            if diff:
                stored["diff"] = diff
            payload = {
                "call_id": call_id,
                "result": injected,
                "is_error": is_error,
                "subagent_id": self.subagent_id,
            }
            if diff:
                payload["diff"] = diff
            await _broadcast(self.session_id, "tool.result", payload, self.broadcaster)
            await self._persist_worker_log(name, args, stored, call_id, is_error, decision, reason or None)
            return {"call_id": call_id, "name": name, "result": injected, "is_error": is_error}
        except Exception as e:
            result = f"[异常] {name} 执行失败: {e}"
            await _broadcast(self.session_id, "tool.result", {"call_id": call_id, "result": result, "is_error": True, "subagent_id": self.subagent_id}, self.broadcaster)
            await self._persist_worker_log(name, args, result, call_id, True, decision, reason or None)
            return {"call_id": call_id, "name": name, "result": result, "is_error": True}

    async def _handle_worker_finish(self, result: str, status: str):
        # 由 finish_worker 触发的回投，需经 batch 聚合
        rec = _subagents.get(self.subagent_id)
        if not rec or not rec.batch_id:
            # 无 batch，直接回投
            await _enqueue_to_main_single(self.session_id, self.subagent_id, result, status, self.main_enqueue, self.broadcaster, self.manager_get)
            return
        batch = _batches.get(rec.batch_id)
        if not batch:
            await _enqueue_to_main_single(self.session_id, self.subagent_id, result, status, self.main_enqueue, self.broadcaster, self.manager_get)
            return
        batch["results"][self.subagent_id] = {"status": status, "result": result}
        # 原子检查是否全部完成（避免并发时双重回投：先检查再广播，无 await 间隙）
        if len(batch["results"]) >= batch["total"]:
            # 仅首个完成的 worker 负责聚合，后续直接返回
            if rec.batch_id not in _batches:
                return
            workers_payload = []
            for wid in batch["workers"]:
                r = batch["results"].get(wid, {"status": "error", "result": "[未完成]"})
                workers_payload.append({"subagent_id": wid, "status": r["status"], "result": r["result"]})
            # 检查主 agent 迟到
            main_agent = None
            if self.manager_get:
                try:
                    main_agent = self.manager_get(self.session_id)
                except Exception:
                    main_agent = None
            late = bool(main_agent and main_agent.state == "done")
            if late:
                for wid in batch["workers"]:
                    rc = _subagents.get(wid)
                    if rc:
                        rc.late = True
            payload = {"batch_id": rec.batch_id, "workers": workers_payload}
            # 先清理防止并发重复
            _batches.pop(rec.batch_id, None)
            if late:
                # 迟到：仅广播供前端观测，不注入主队列（不唤醒已 done 的主 agent，PLAN §2.4）
                await _broadcast(self.session_id, "worker.batch_done", payload, self.broadcaster)
                await _broadcast(self.session_id, "worker.status", {"workers": get_workers(self.session_id)}, self.broadcaster)
                return
            # 注入主队列
            try:
                await self.main_enqueue({"type": "worker_batch_done", "payload": payload})
            except Exception as e:
                logger.debug("Enqueue batch_done failed: %s", e)
            await _broadcast(self.session_id, "worker.batch_done", payload, self.broadcaster)
            await _broadcast(self.session_id, "worker.status", {"workers": get_workers(self.session_id)}, self.broadcaster)
            return
        # 未完成则仅广播状态
        await _broadcast(self.session_id, "worker.status", {"workers": get_workers(self.session_id)}, self.broadcaster)

    # _enqueue_to_main_inner 已替代此逻辑，保留兼容空实现
    async def _enqueue_to_main(self, result: str, status: str):
        await self._enqueue_to_main_inner(result, status)


async def _enqueue_to_main_single(session_id: str, subagent_id: str, result: str, status: str, main_enqueue, broadcaster, manager_get):
    rec = _subagents.get(subagent_id)
    late = False
    main_agent = None
    if manager_get:
        try:
            main_agent = manager_get(session_id)
        except Exception:
            main_agent = None
    if main_agent and main_agent.state == "done":
        late = True
        if rec:
            rec.late = True
    if late:
        # 迟到：仍广播但不唤醒主 agent（仅存储）
        logger.info("SubAgent late result: %s session=%s", subagent_id, session_id)
        return
    payload = {"subagent_id": subagent_id, "kind": rec.kind if rec else "unknown", "status": status, "result": result}
    try:
        # 主 agent 队列类型：subagent_result 或 worker 单条兼容
        if rec and rec.kind == "worker":
            # 单 worker 无 batch 时也走 batch_done 单条
            await main_enqueue({"type": "worker_batch_done", "payload": {"batch_id": rec.batch_id or subagent_id, "workers": [payload]}})
            await _broadcast(session_id, "worker.batch_done", {"batch_id": rec.batch_id or subagent_id, "workers": [payload]}, broadcaster)
        else:
            # subagent.done 已由 _finish 广播，此处仅注入主 agent 队列
            await main_enqueue({"type": "subagent_result", "result": result, "subagent_id": subagent_id})
    except Exception as e:
        logger.debug("Enqueue to main failed: %s", e)


# ---------- 对外 spawn 接口 ----------

async def spawn_interactive(
    session_id: str,
    behavior_desc: str,
    goal: str,
    main_history: list[dict],
    summary: str | None,
    broadcaster,
    main_enqueue,
    manager_get,
) -> str:
    if get_active_count(session_id) >= settings.subagent_max_concurrency:
        return f"[拒绝] 已达并发上限 {settings.subagent_max_concurrency}，请稍后重试"
    subagent_id = f"sub_{uuid.uuid4().hex[:8]}"
    task_desc = goal or behavior_desc or "交互任务"
    snapshot = _snapshot_history(main_history)
    rec = SubAgentRecord(
        subagent_id=subagent_id,
        session_id=session_id,
        kind="interactive",
        status="running",
        task=task_desc,
        behavior_desc=behavior_desc,
    )
    _subagents[subagent_id] = rec
    _session_index.setdefault(session_id, set()).add(subagent_id)
    loop = SubAgentLoop(session_id, subagent_id, "interactive", task_desc, behavior_desc, snapshot, summary, broadcaster, main_enqueue, manager_get=manager_get)
    _loops[subagent_id] = loop
    task = asyncio.create_task(loop.run())
    _tasks[subagent_id] = task
    await _broadcast(session_id, "subagent.opened", {"subagent_id": subagent_id, "kind": "interactive", "session_id": session_id, "task": task_desc}, broadcaster)
    try:
        from app import persist as persist_mod

        await persist_mod.upsert_subagent_run(
            main_session_id=session_id,
            subagent_id=subagent_id,
            kind="interactive",
            behavior_desc=behavior_desc,
            goal=goal,
            status="running",
        )
    except Exception:
        logger.debug("persist subagent_run failed", exc_info=True)
    logger.info("Spawn interactive: %s session=%s", subagent_id, session_id)
    return f"[已派生交互型子 agent] id={subagent_id} 任务: {task_desc} — 侧栏已打开，等待用户对话，完成后将自动回投主 agent。"


async def open_interactive_for_user(
    session_id: str,
    main_history: list[dict],
    summary: str | None,
    broadcaster,
    main_enqueue,
    manager_get,
) -> str:
    """用户从顶栏呼出交互型侧栏。已有运行中的则复用，否则新开。成功返回 subagent_id，失败返回 [拒绝]/[错误]。"""
    running = [p for p in get_panels(session_id) if p.get("status") == "running"]
    if running:
        return running[0]["subagent_id"]
    msg = await spawn_interactive(
        session_id,
        "用户侧栏对话",
        "与用户对话；需要时调用 finish_subagent 将摘要回投主 agent",
        main_history,
        summary,
        broadcaster,
        main_enqueue,
        manager_get,
    )
    if msg.startswith(("[拒绝]", "[错误]")):
        return msg
    running = [p for p in get_panels(session_id) if p.get("status") == "running"]
    return running[0]["subagent_id"] if running else msg


async def spawn_worker_batch(
    session_id: str,
    tasks: list[str],
    main_history: list[dict],
    summary: str | None,
    broadcaster,
    main_enqueue,
    manager_get,
    constraints: str | None = None,
) -> str:
    if not tasks:
        return "[错误] 未提供任务"
    if len(tasks) > settings.max_workers_per_turn:
        return f"[拒绝] 单轮派生不超过 {settings.max_workers_per_turn}，本次请求 {len(tasks)}"
    if get_active_count(session_id) + len(tasks) > settings.subagent_max_concurrency:
        return f"[拒绝] 总并发不超过 {settings.subagent_max_concurrency}，当前活跃 {get_active_count(session_id)}，请求 {len(tasks)}"
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    snapshot = _snapshot_history(main_history)
    spawned = []
    _batches[batch_id] = {"workers": [], "results": {}, "total": len(tasks)}
    for t in tasks:
        subagent_id = f"wk_{uuid.uuid4().hex[:8]}"
        rec = SubAgentRecord(
            subagent_id=subagent_id,
            session_id=session_id,
            kind="worker",
            status="running",
            task=t,
            behavior_desc=constraints or "",
            batch_id=batch_id,
        )
        _subagents[subagent_id] = rec
        _session_index.setdefault(session_id, set()).add(subagent_id)
        _batches[batch_id]["workers"].append(subagent_id)
        loop = SubAgentLoop(session_id, subagent_id, "worker", t, constraints or "", snapshot, summary, broadcaster, main_enqueue, batch_id=batch_id, manager_get=manager_get)
        _loops[subagent_id] = loop
        task_obj = asyncio.create_task(loop.run())
        _tasks[subagent_id] = task_obj
        spawned.append(subagent_id)
        await _broadcast(session_id, "subagent.opened", {"subagent_id": subagent_id, "kind": "worker", "session_id": session_id, "task": t[:80]}, broadcaster)
        try:
            from app import persist as persist_mod

            await persist_mod.upsert_subagent_run(
                main_session_id=session_id,
                subagent_id=subagent_id,
                kind="worker",
                behavior_desc=constraints or "",
                goal=t,
                status="running",
            )
        except Exception:
            logger.debug("persist worker run failed", exc_info=True)
    await _broadcast(session_id, "worker.status", {"workers": get_workers(session_id)}, broadcaster)
    logger.info("Spawn workers batch=%s count=%d session=%s", batch_id, len(tasks), session_id)
    return f"[已派生后台工作型] batch_id={batch_id} workers={spawned} 任务: {tasks} — 后台并发执行中，完成将批量回投。"


async def handle_subagent_response(session_id: str, subagent_id: str, content: str) -> bool:
    loop = _loops.get(subagent_id)
    if not loop:
        return False
    if loop.session_id != session_id:
        return False
    await loop.enqueue_user(content)
    # 回显子 agent 侧消息
    await _broadcast(session_id, "subagent.message", {"subagent_id": subagent_id, "role": "user", "content": content}, loop.broadcaster)
    return True


async def stop_subagent(subagent_id: str) -> bool:
    """定向终止子 agent（PLAN §3 agent.stop 对子 agent 的语义）"""
    loop = _loops.get(subagent_id)
    rec = _subagents.get(subagent_id)
    if not loop or not rec or rec.status != "running":
        return False
    loop._stop_requested = True
    # 拒绝该子 agent 的 pending 审批并回收其 shell 进程组
    for p in gate.list_pending(rec.session_id):
        if p.agent_id == subagent_id:
            gate.resolve(p.approval_id, "reject", reason="stopped")
    try:
        from app.tools.shell import kill_shell_group

        kill_shell_group(subagent_id)
    except Exception as e:
        logger.debug("Kill subagent shell group failed: %s", e)
    task = _tasks.get(subagent_id)
    if task and not task.done():
        task.cancel()
    logger.info("SubAgent stopped by user: %s session=%s", subagent_id, rec.session_id)
    return True


async def stop_session_subagents(session_id: str) -> int:
    """终止某会话下全部子 agent（删除会话前）。"""
    ids = list(_session_index.get(session_id) or [])
    n = 0
    tasks = []
    for sid in ids:
        t = _tasks.get(sid)
        if await stop_subagent(sid):
            n += 1
            if t is not None:
                tasks.append(t)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return n


def purge_session(session_id: str) -> None:
    """会话删除后清理该会话全部内存登记（records/loops/tasks/batches/索引），防泄漏。"""
    ids = list(_session_index.pop(session_id, set()) or [])
    batch_ids: set[str] = set()
    for sid in ids:
        rec = _subagents.pop(sid, None)
        if rec and rec.batch_id:
            batch_ids.add(rec.batch_id)
        _loops.pop(sid, None)
        _tasks.pop(sid, None)
    for bid in batch_ids:
        _batches.pop(bid, None)


def list_panels_and_workers(session_id: str) -> tuple[list[dict], list[dict]]:
    return get_panels(session_id), get_workers(session_id)
