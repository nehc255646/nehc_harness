"""审查修复回归测试 — 流式 args 解析/迟到不唤醒/history 去重/每轮派生上限/取消进程回收"""

import asyncio
import contextlib

from app.agent import subagent as sa
from app.agent.context import accumulate_tool_calls, parse_tool_calls
from app.agent.loop import AgentLoop
from app.core.config import settings
from app.tools import shell as shell_mod


def test_accumulate_tool_calls_string_fragments():
    """字符串碎片逐段拼接后可正常 JSON 解析"""
    acc: dict[int, dict] = {}
    accumulate_tool_calls(acc, {"tool_call_chunks": [{"name": "shell", "args": '{"comm', "id": "c1", "index": 0}]})
    accumulate_tool_calls(acc, {"tool_call_chunks": [{"name": None, "args": 'and":"ls -la"}', "id": None, "index": 0}]})
    tcs = parse_tool_calls(acc)
    assert tcs == [{"name": "shell", "args": {"command": "ls -la"}, "id": "c1"}]


def test_accumulate_tool_calls_parsed_args_not_corrupted():
    """chunk 同时携带碎片与已解析完整 tool_calls 时，最终 args 保持 dict（不被 __raw 损坏）"""
    acc: dict[int, dict] = {}
    accumulate_tool_calls(acc, {"tool_call_chunks": [{"name": "shell", "args": '{"comm', "id": "c1", "index": 0}]})
    accumulate_tool_calls(
        acc,
        {
            "tool_call_chunks": [{"name": None, "args": 'and":"ls"}', "id": None, "index": 0}],
            "tool_calls": [{"name": "shell", "args": {"command": "ls"}, "id": "c1", "index": 0}],
        },
    )
    tcs = parse_tool_calls(acc)
    assert tcs[0]["args"] == {"command": "ls"}
    assert "__raw" not in tcs[0]["args"]


def test_parse_tool_calls_keeps_dict_args():
    """非流式回退路径：args 已是 dict 时直接保留，不做 str() 转换"""
    acc = {0: {"name": "write", "args": {"path": "a.txt", "content": "x"}, "id": "w1", "index": 0}}
    tcs = parse_tool_calls(acc)
    assert tcs[0]["args"] == {"path": "a.txt", "content": "x"}


async def test_late_worker_batch_not_wake_main():
    """主 agent 已 done 时，工作型批次仅广播、不注入主队列（迟到不唤醒，PLAN §2.4）"""
    got: list[dict] = []

    async def capture_enqueue(ev):
        got.append(ev)

    main = AgentLoop("ut_late")
    main.state = "done"
    loop = sa.SubAgentLoop(
        session_id="ut_late",
        subagent_id="wk_late1",
        kind="worker",
        task="任务",
        behavior_desc="",
        snapshot=[],
        summary=None,
        broadcaster=None,
        main_enqueue=capture_enqueue,
        batch_id="batch_late",
        manager_get=lambda _sid: main,
    )
    sa._subagents["wk_late1"] = sa.SubAgentRecord(
        subagent_id="wk_late1", session_id="ut_late", kind="worker", status="running", task="任务", batch_id="batch_late"
    )
    sa._session_index.setdefault("ut_late", set()).add("wk_late1")
    sa._batches["batch_late"] = {"workers": ["wk_late1"], "results": {}, "total": 1}
    try:
        await loop._handle_worker_finish("结果", "done")
        assert got == [], "迟到结果不得唤醒主 agent"
        assert "batch_late" not in sa._batches, "迟到批次也应清理"
        assert sa._subagents["wk_late1"].late is True
    finally:
        sa._subagents.pop("wk_late1", None)
        sa._session_index.pop("ut_late", None)


async def test_interactive_history_no_double_append(monkeypatch):
    """交互型纯文本回复在 history 中只出现一次（_emit_text 已入 history）"""
    monkeypatch.setattr(sa, "_INTERACTIVE_IDLE_TIMEOUT", 0.2)
    loop = sa.SubAgentLoop(
        session_id="ut_dedup_int",
        subagent_id="sub_d1",
        kind="interactive",
        task="任务",
        behavior_desc="",
        snapshot=[],
        summary=None,
        broadcaster=None,
        main_enqueue=None,
        manager_get=lambda _s: None,
    )
    loop.executor._llm = None  # 强制 heuristic
    sa._subagents["sub_d1"] = sa.SubAgentRecord(
        subagent_id="sub_d1", session_id="ut_dedup_int", kind="interactive", status="running", task="任务"
    )
    sa._session_index.setdefault("ut_dedup_int", set()).add("sub_d1")
    task = asyncio.create_task(loop.run())
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if sa._subagents["sub_d1"].status != "running":
                break
        assistant = [m for m in loop.history if m.get("role") == "assistant"]
        assert len(assistant) == 1, f"应恰好 1 条 assistant 消息，实际 {len(assistant)}"
        assert sa._subagents["sub_d1"].status == "done"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        sa._subagents.pop("sub_d1", None)
        sa._session_index.pop("ut_dedup_int", None)


async def test_per_turn_spawn_limit():
    """单轮派生上限跨多次 spawn 调用累计（防绕过 MAX_WORKERS_PER_TURN）"""
    ag = AgentLoop("ut_limit")
    assert ag._turn_spawned == 0
    ag._turn_spawned = settings.max_workers_per_turn
    res1 = await ag._handle_spawn_tool("spawn_worker", {"task": "t"})
    assert "拒绝" in res1
    res2 = await ag._handle_spawn_tool("spawn_workers", {"tasks": ["a", "b", "c"]})
    assert "拒绝" in res2
    assert ag._turn_spawned == settings.max_workers_per_turn, "被拒请求不应累计"


async def test_shell_pgid_kept_on_cancel():
    """shell 被取消时 pgid 保留登记，kill_shell_group 可兜底回收"""
    task = asyncio.create_task(shell_mod.shell_async("sleep 5", timeout=10, group="ut_cancel"))
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert shell_mod._active_pgs.get("ut_cancel"), "取消后 pgid 应保留供回收"
    shell_mod.kill_shell_group("ut_cancel")
    await asyncio.sleep(0.05)
    assert not shell_mod._active_pgs.get("ut_cancel")
