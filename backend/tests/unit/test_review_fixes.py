"""审查修复回归测试 — 流式 args 解析/迟到不唤醒/history 去重/每轮派生上限/取消进程回收"""

import asyncio
import contextlib

from app.agent import subagent as sa
from app.agent.context import (
    accumulate_tool_calls,
    normalize_tool_args,
    parse_tool_calls,
    shell_command,
)
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


def test_accumulate_empty_complete_does_not_wipe_fragments():
    acc: dict[int, dict] = {}
    accumulate_tool_calls(acc, {"tool_call_chunks": [{"name": "shell", "args": '{"command":"df -h"}', "id": "c1", "index": 0}]})
    accumulate_tool_calls(acc, {"tool_calls": [{"name": "shell", "args": {}, "id": "c1", "index": 0}]})
    tcs = parse_tool_calls(acc)
    assert tcs[0]["args"]["command"] == "df -h"


def test_accumulate_complete_without_index_does_not_duplicate():
    acc: dict[int, dict] = {}
    accumulate_tool_calls(acc, {"tool_call_chunks": [{"name": "shell", "args": '{"command":"uname"}', "id": "c1", "index": 0}]})
    accumulate_tool_calls(acc, {"tool_calls": [{"name": "shell", "args": {"command": "uname"}, "id": "c1"}]})
    tcs = parse_tool_calls(acc)
    assert len(tcs) == 1
    assert tcs[0]["args"] == {"command": "uname"}


def test_accumulate_two_complete_calls_without_index():
    acc: dict[int, dict] = {}
    accumulate_tool_calls(
        acc,
        {
            "tool_calls": [
                {"name": "shell", "args": {"command": "echo a"}, "id": "c1"},
                {"name": "shell", "args": {"command": "echo b"}, "id": "c2"},
            ]
        },
    )
    tcs = parse_tool_calls(acc)
    assert [t["args"]["command"] for t in tcs] == ["echo a", "echo b"]


def test_normalize_tool_args_recovers_command():
    assert normalize_tool_args({"__raw": '{"command": "echo hi"}'}) == {"command": "echo hi"}
    assert normalize_tool_args("uname -a") == {"command": "uname -a"}
    assert shell_command({"__raw": '{"command": "df -h"}'}) == "df -h"
    assert shell_command({"command": "  "}) == ""


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
        await loop.enqueue_user("hello")
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
    res1 = await ag._handle_spawn_tool("spawn_worker", {"task": "t", "done_when": "完成切片 t"})
    assert "拒绝" in res1
    res2 = await ag._handle_spawn_tool(
        "spawn_workers",
        {"tasks": ["a", "b", "c"], "done_when": ["完成 a", "完成 b", "完成 c"]},
    )
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


async def test_shell_group_key_scoped_per_session():
    """不同会话的主 agent 进程组 key 必须不同（防跨会话 stop 误杀）"""
    a = AgentLoop("ut_sess_a")
    b = AgentLoop("ut_sess_b")
    assert a._shell_group() != b._shell_group()
    assert a.session_id in a._shell_group()


async def test_purge_session_clears_registries():
    """会话删除后子 agent 注册表/批次/索引全部清理，防内存泄漏"""
    sa._subagents["wk_purge1"] = sa.SubAgentRecord(
        subagent_id="wk_purge1", session_id="ut_purge", kind="worker", status="done", task="t", batch_id="batch_purge"
    )
    sa._session_index.setdefault("ut_purge", set()).add("wk_purge1")
    sa._batches["batch_purge"] = {"workers": ["wk_purge1"], "results": {}, "total": 1}
    sa._loops["wk_purge1"] = object()  # type: ignore[assignment]
    sa.purge_session("ut_purge")
    assert "wk_purge1" not in sa._subagents
    assert "wk_purge1" not in sa._loops
    assert "wk_purge1" not in sa._tasks
    assert "batch_purge" not in sa._batches
    assert "ut_purge" not in sa._session_index


async def test_shell_empty_command_rejected(monkeypatch):
    """空/不可解析的 shell 命令不执行（防 __raw 兜底导致空命令静默成功）"""
    ag = AgentLoop("ut_empty_shell")

    async def noop_log(*_a, **_k):
        return None

    monkeypatch.setattr(ag, "_persist_tool_log", noop_log)
    res = await ag._execute_tool("shell", {"command": ""}, "c_empty", "config_allow", "")
    assert res["is_error"] is True
    assert "空" in res["result"]


async def test_approve_similar_empty_shell_adds_no_rule():
    from app.permissions.gate import gate

    sid = "ut_empty_rule"
    gate.clear_session_rules(sid)
    aid, fut = await gate.request_approval(sid, "main", "shell", {}, "shell")
    assert gate.resolve(aid, "approve_similar") is True
    assert gate.get_session_rules(sid) == []
    approved, decision, _reason = await fut
    assert approved is True
    assert decision == "approve_similar"


async def test_remove_session_rule():
    from app.permissions.gate import gate

    sid = "ut_revoke_rule"
    gate.clear_session_rules(sid)
    gate.add_session_rule(sid, {"kind": "shell_prefix", "pattern": "echo hello"}, persist=False)
    gate.add_session_rule(sid, {"kind": "tool", "pattern": "write"}, persist=False)
    assert gate.remove_session_rule(sid, "shell_prefix", "echo hello", persist=False) is True
    rules = gate.get_session_rules(sid)
    assert rules == [{"kind": "tool", "pattern": "write"}]
    assert gate.remove_session_rule(sid, "shell_prefix", "echo hello", persist=False) is False
    gate.clear_session_rules(sid)


async def test_empty_shell_skips_approval():
    ag = AgentLoop("ut_empty_skip")
    reqs: list[dict] = []

    async def capture(_sid, event, payload):
        if event == "approval.request":
            reqs.append(payload)

    ag.set_broadcaster(capture)
    out = await ag._dispatch_tools([{"id": "c0", "name": "shell", "args": {}}])
    assert out[0]["is_error"] is True
    assert reqs == []


async def test_shell_recovers_command_from_raw(monkeypatch):
    ag = AgentLoop("ut_raw_cmd")
    ran: list[str] = []

    async def fake_shell(command, **_k):
        ran.append(command)
        return "ok", 0

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.tools.shell.shell_async", fake_shell)
    monkeypatch.setattr(ag, "_persist_tool_log", noop)
    res = await ag._execute_tool("shell", {"__raw": '{"command": "uname -a"}'}, "c_raw", "config_allow", "")
    assert res["is_error"] is False
    assert ran == ["uname -a"]


async def test_sequential_approve_similar_covers_later_shell(monkeypatch):
    from app.permissions.gate import gate

    sid = "ut_seq_appr"
    ag = AgentLoop(sid)
    gate.clear_session_rules(sid)
    ran: list[str] = []

    async def fake_shell(command, **_k):
        ran.append(command)
        return f"ok:{command}", 0

    async def noop(*_a, **_k):
        return None

    async def capture(_sid, event, payload):
        if event == "approval.request":
            gate.resolve(payload["approval_id"], "approve_similar")

    monkeypatch.setattr("app.tools.shell.shell_async", fake_shell)
    monkeypatch.setattr(ag, "_persist_tool_log", noop)
    ag.set_broadcaster(capture)
    out = await ag._dispatch_tools(
        [
            {"id": "c1", "name": "shell", "args": {"command": "echo hello world"}},
            {"id": "c2", "name": "shell", "args": {"command": "echo hello there"}},
        ]
    )
    assert [r["is_error"] for r in out] == [False, False]
    assert ran == ["echo hello world", "echo hello there"]
    gate.clear_session_rules(sid)
