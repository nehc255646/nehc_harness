"""loop/subagent 修复回归测试 — done 唤醒/文本去重/子 agent 终止"""

import asyncio

from app.agent import subagent as sa
from app.agent.loop import AgentLoop
from app.agent.prompts import (
    INTERACTIVE_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    WORKER_SYSTEM_PROMPT,
)
from app.core.config import settings
from app.tools.registry import PLAN_TOOLS, TOOLS


def _force_heuristic(agent: AgentLoop) -> None:
    # 置空 LLM 强制走 heuristic，测试离线确定
    agent.executor._llm = None


async def test_enqueue_wakes_done_agent():
    """done 终态后新消息必须唤醒新一轮 run（PLAN §2.5 done 非死局）"""
    ag = AgentLoop("ut_wake")
    _force_heuristic(ag)
    ag.state = "done"
    await ag.enqueue({"type": "user_message", "content": "执行 ls"})
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(m.get("role") == "user" for m in ag.history):
            break
    assert any(m.get("role") == "user" for m in ag.history)
    assert ag.state == "running"
    await ag.stop()


async def test_midrun_user_message_injected_after_current_node():
    """工作中发来的用户消息在本轮工具结束后写入历史，下一轮模型能看到（与子 agent 回投同队列）。"""
    ag = AgentLoop("ut_inject")
    seen: list[list[str]] = []

    async def fake_call(messages):
        users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
        seen.append(users)
        if len(seen) == 1:
            await ag.queue.put({"type": "user_message", "content": "改用 python"})
            return {
                "text": "先看文件",
                "tool_calls": [{"name": "glob", "args": {"pattern": "*"}, "id": "c1"}],
                "streamed": False,
            }
        return {"text": "好", "tool_calls": [], "streamed": False}

    async def fake_dispatch(tool_calls):
        return [{"call_id": tc.get("id") or "", "name": tc.get("name") or "", "result": "ok"} for tc in tool_calls]

    ag._call_model = fake_call  # type: ignore[method-assign]
    ag._dispatch_tools = fake_dispatch  # type: ignore[method-assign]
    await ag.enqueue({"type": "user_message", "content": "写代码"})
    for _ in range(200):
        await asyncio.sleep(0.02)
        if ag.state == "idle" and len(seen) >= 2:
            break
    assert len(seen) >= 2, f"应进入第二轮, seen={seen}"
    assert any("写代码" in u for u in seen[0])
    assert not any("改用 python" in u for u in seen[0])
    assert any("改用 python" in u for u in seen[1])
    await ag.stop()


async def test_emit_message_record_flag():
    """record=False 不入 history（tool_calls 路径由 assistant_msg 统一回填，避免重复）"""
    ag = AgentLoop("ut_emit")
    _force_heuristic(ag)
    await ag._emit_message("part-a", record=False)
    assert ag.history == []
    await ag._emit_message("part-b", record=True)
    assert len(ag.history) == 1
    assert ag.history[0]["content"] == "part-b"


async def test_heuristic_tool_call_text_not_duplicated():
    """heuristic + tool_calls 路径：history 中文本只出现一次"""
    ag = AgentLoop("ut_dedup")
    _force_heuristic(ag)
    # rm -rf 走 blocked 分支，无需审批即可完成整轮
    await ag.enqueue({"type": "user_message", "content": "执行 rm -rf /"})
    for _ in range(150):
        await asyncio.sleep(0.02)
        if ag.state == "idle":
            break
    assistant_texts = [m["content"] for m in ag.history if m.get("role") == "assistant"]
    assert assistant_texts, "应有 assistant 回复"
    assert len(assistant_texts) == len(set(assistant_texts)), f"文本重复: {assistant_texts}"
    await ag.stop()


async def test_open_interactive_for_user_reuses_running():
    """用户再次呼出时复用运行中的交互型，不另开一个"""

    async def noop_enqueue(_ev):
        return None

    sid = "ut_open_reuse"
    first = await sa.open_interactive_for_user(sid, [], None, None, noop_enqueue, lambda _sid: None)
    assert first.startswith("sub_")
    second = await sa.open_interactive_for_user(sid, [], None, None, noop_enqueue, lambda _sid: None)
    assert second == first
    await sa.stop_session_subagents(sid)
    await asyncio.sleep(0.2)


async def test_interactive_waits_for_user_before_model():
    """打开交互型不调模型，直到侧栏发来用户消息"""

    async def noop_enqueue(_ev):
        return None

    sid = "ut_wait_user"
    opened = await sa.open_interactive_for_user(sid, [], None, None, noop_enqueue, lambda _s: None)
    loop = sa._loops[opened]
    loop.executor._llm = None
    await asyncio.sleep(0.35)
    assert sa._subagents[opened].status == "running"
    assert not any(m.get("role") == "assistant" for m in loop.history)
    await loop.enqueue_user("ping")
    for _ in range(80):
        await asyncio.sleep(0.05)
        if any(m.get("role") == "assistant" for m in loop.history):
            break
    assert any(m.get("role") == "assistant" for m in loop.history)
    await sa.stop_session_subagents(sid)
    await asyncio.sleep(0.2)


async def test_open_interactive_first_message_enqueued():
    """首条侧栏消息随 open 进入交互型 history"""

    async def noop_enqueue(_ev):
        return None

    sid = "ut_first_msg"
    opened = await sa.open_interactive_for_user(
        sid, [], None, None, noop_enqueue, lambda _s: None, first_message="hello sidebar"
    )
    loop = sa._loops[opened]
    for _ in range(40):
        await asyncio.sleep(0.05)
        users = [m.get("content") for m in loop.history if m.get("role") == "user"]
        if any("hello sidebar" in str(c) for c in users):
            break
    users = [m.get("content") for m in loop.history if m.get("role") == "user"]
    assert any("hello sidebar" in str(c) for c in users)
    await sa.stop_session_subagents(sid)
    await asyncio.sleep(0.2)


async def test_stop_interactive_subagent():
    """agent.stop 可定向终止交互型子 agent，状态置 done 并标记已停止"""
    async def noop_enqueue(_ev):
        return None

    res = await sa.spawn_interactive("ut_stop_int", "行为描述", "目标", [], None, None, noop_enqueue, lambda _sid: None)
    sid = res.split("id=")[1].split(" ")[0]
    # 首轮输出引导语后进入等待用户输入
    await asyncio.sleep(0.3)
    assert sa._subagents[sid].status == "running"
    assert await sa.stop_subagent(sid) is True
    await asyncio.sleep(0.3)
    assert sa._subagents[sid].status == "done"
    assert "已停止" in (sa._subagents[sid].result or "")
    assert await sa.stop_subagent(sid) is False


async def test_stop_session_subagents_cancels_running():
    async def noop_enqueue(_ev):
        return None

    res = await sa.spawn_interactive("ut_stop_sess", "行为", "目标", [], None, None, noop_enqueue, lambda _sid: None)
    sid = res.split("id=")[1].split(" ")[0]
    await asyncio.sleep(0.2)
    n = await sa.stop_session_subagents("ut_stop_sess")
    assert n >= 1
    await asyncio.sleep(0.2)
    assert sa._subagents[sid].status == "done"


async def test_unresolved_executor_does_not_heuristic():
    from app.agent.executor import Executor

    ag = AgentLoop("ut_unresolved")
    ag.executor = Executor(unresolved=True)
    errors: list[dict] = []

    async def capture(_sid, event, payload):
        if event == "error":
            errors.append(payload)

    ag.set_broadcaster(capture)
    await ag.start()
    await ag.enqueue({"type": "user_message", "content": "hello"})
    for _ in range(80):
        await asyncio.sleep(0.02)
        if errors or ag.state == "error":
            break
    assert ag.state == "error"
    assert any(e.get("code") == "MODEL_ERROR" or getattr(e.get("code"), "value", None) == "MODEL_ERROR" for e in errors)
    await ag.stop()


async def test_worker_single_batch_completes_and_cleans():
    """单 worker 完成后批次聚合并清理，结果回投主 agent 队列"""
    import uuid

    from app.permissions.gate import gate

    got: list[dict] = []
    sid = f"ut_wb_{uuid.uuid4().hex[:8]}"

    async def capture_enqueue(ev):
        got.append(ev)

    gate.add_session_rule(sid, {"kind": "shell_prefix", "pattern": "echo"})
    try:
        res = await sa.spawn_worker_batch(sid, ["列出演示目录"], [], None, None, capture_enqueue, lambda _sid: None)
        assert "已派生" in res
        wid = sa.get_workers(sid)[0]["subagent_id"]
        batch_id = sa.get_workers(sid)[0]["batch_id"]
        for _ in range(100):
            await asyncio.sleep(0.05)
            if sa.get_workers(sid)[0]["state"] != "running":
                break
        assert sa.get_workers(sid)[0]["state"] == "done"
        assert batch_id not in sa._batches, "批次完成后应清理"
        assert got, "batch_done 应回投主 agent 队列"
        assert got[0]["type"] == "worker_batch_done"
        assert got[0]["payload"]["workers"][0]["subagent_id"] == wid
    finally:
        await sa.stop_session_subagents(sid)
        sa.purge_session(sid)
        gate.clear_session_rules(sid)


def test_system_prompt_switches_with_work_mode():
    ag = AgentLoop("ut_sys")
    ag.history = [{"role": "user", "content": "hi"}]
    auto_msgs = ag._build_messages()
    assert auto_msgs[0]["role"] == "system"
    assert auto_msgs[0]["content"] == SYSTEM_PROMPT
    assert "Work mode: auto" in auto_msgs[0]["content"]
    ag.set_work_mode("plan")
    plan_msgs = ag._build_messages()
    assert plan_msgs[0]["content"] == PLAN_SYSTEM_PROMPT
    assert "Work mode: plan" in plan_msgs[0]["content"]
    assert plan_msgs[0]["content"] != SYSTEM_PROMPT
    assert ag._bound_tools() == PLAN_TOOLS
    ag.set_work_mode("auto")
    assert ag._bound_tools() == TOOLS


def test_subagent_system_prompts_are_distinct_english():
    assert "BACKGROUND WORKER" in WORKER_SYSTEM_PROMPT
    assert "INTERACTIVE SIDEBAR" in INTERACTIVE_SYSTEM_PROMPT
    assert "finish_worker" in WORKER_SYSTEM_PROMPT
    assert "finish_subagent" in INTERACTIVE_SYSTEM_PROMPT
    assert "sidebar" in INTERACTIVE_SYSTEM_PROMPT.lower()
    assert "Do not ask the user questions" in WORKER_SYSTEM_PROMPT
    assert "no file, shell, or spawn tools" in INTERACTIVE_SYSTEM_PROMPT
    assert WORKER_SYSTEM_PROMPT != INTERACTIVE_SYSTEM_PROMPT
    assert "You are the MAIN" in SYSTEM_PROMPT
    assert "You are the MAIN" in PLAN_SYSTEM_PROMPT


def test_heuristic_plan_does_not_emit_shell():
    ag = AgentLoop("ut_plan_h")
    ag.set_work_mode("plan")
    res = ag._heuristic_fallback([{"role": "user", "content": "执行 echo hello"}])
    assert res["tool_calls"] == []
    assert "plan" in res["text"]


async def test_spawn_rejects_task_equal_to_user_goal():
    ag = AgentLoop("ut_spawn_clone")
    ag.history = [{"role": "user", "content": "重构整个工作区"}]
    res = await ag._handle_spawn_tool("spawn_worker", {"task": "重构整个工作区"})
    assert "拒绝" in res
    assert ag._turn_spawned == 0


async def test_plan_mode_blocks_spawn_dispatch():
    ag = AgentLoop("ut_plan_spawn")
    ag.set_work_mode("plan")
    out = await ag._dispatch_tools(
        [{"id": "c1", "name": "spawn_worker", "args": {"task": "x"}}]
    )
    assert out[0]["is_error"] is True
    assert "plan" in out[0]["result"]


async def test_plan_mode_blocks_write_dispatch():
    ag = AgentLoop("ut_plan_write")
    ag.set_work_mode("plan")
    out = await ag._dispatch_tools(
        [{"id": "c2", "name": "write", "args": {"path": "a.txt", "content": "x"}}]
    )
    assert out[0]["is_error"] is True
    assert "plan" in out[0]["result"]


async def test_get_or_create_waits_for_hydrate():
    from app.agent.manager import AgentManager

    mgr = AgentManager()
    sid = "ut_hydrate_lock"
    order: list[str] = []

    async def slow_hydrate(self):
        order.append("hydrate_start")
        await asyncio.sleep(0.05)
        self.history = [{"role": "user", "content": "fromdb"}]
        order.append("hydrate_end")

    orig = AgentLoop.hydrate_from_db
    AgentLoop.hydrate_from_db = slow_hydrate  # type: ignore[method-assign]
    try:

        async def use():
            ag = await mgr.get_or_create(sid)
            order.append(f"got:{len(ag.history)}")
            return ag

        a, b = await asyncio.gather(use(), use())
        assert a is b
        assert a.history[0]["content"] == "fromdb"
        assert order[0] == "hydrate_start"
        assert "hydrate_end" in order
        assert order.index("hydrate_end") < order.index("got:1")
    finally:
        AgentLoop.hydrate_from_db = orig  # type: ignore[method-assign]
        await mgr.drop(sid)


async def test_manager_stop_halts_workers():
    from app.agent.manager import AgentManager
    from app.permissions.gate import gate

    mgr = AgentManager()
    sid = "ut_stop_workers"
    agent = AgentLoop(sid)
    mgr._agents[sid] = agent
    gate.add_session_rule(sid, {"kind": "shell_prefix", "pattern": "echo"})

    async def noop_enqueue(_ev):
        return None

    res = await sa.spawn_worker_batch(sid, ["sleep 8"], [], None, None, noop_enqueue, mgr.get)
    assert "已派生" in res
    wid = sa.get_workers(sid)[0]["subagent_id"]
    await asyncio.sleep(0.15)
    await mgr.stop(sid)
    await asyncio.sleep(0.4)
    rec = sa._subagents.get(wid)
    assert rec is not None
    assert rec.status != "running"
    gate.clear_session_rules(sid)


async def test_worker_plan_mode_blocks_write():
    main = AgentLoop("ut_wk_plan")
    main.set_work_mode("plan")
    loop = sa.SubAgentLoop(
        session_id="ut_wk_plan",
        subagent_id="wk_plan1",
        kind="worker",
        task="写文件",
        behavior_desc="",
        snapshot=[],
        summary=None,
        broadcaster=None,
        main_enqueue=None,
        manager_get=lambda _sid: main,
        work_mode="auto",
    )
    out = await loop._dispatch_worker_tools(
        [{"id": "c1", "name": "write", "args": {"path": "a.txt", "content": "x"}}]
    )
    assert out[0]["is_error"] is True
    assert "plan" in out[0]["result"]


async def test_spawn_lock_respects_concurrency(monkeypatch):
    monkeypatch.setattr(settings, "subagent_max_concurrency", 1)

    async def noop_enqueue(_ev):
        return None

    sid = "ut_spawn_lock"
    first = await sa.spawn_interactive(sid, "a", "a", [], None, None, noop_enqueue, lambda _s: None)
    assert "已派生" in first
    second = await sa.spawn_interactive(sid, "b", "b", [], None, None, noop_enqueue, lambda _s: None)
    assert "拒绝" in second
    await sa.stop_session_subagents(sid)
    await asyncio.sleep(0.2)
