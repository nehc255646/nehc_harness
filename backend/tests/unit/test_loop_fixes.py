"""loop/subagent 修复回归测试 — done 唤醒/文本去重/子 agent 终止"""

import asyncio

from app.agent import subagent as sa
from app.agent.loop import AgentLoop


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
    from app.permissions.gate import gate

    got: list[dict] = []

    async def capture_enqueue(ev):
        got.append(ev)

    gate.add_session_rule("ut_wb", {"kind": "shell_prefix", "pattern": "echo"})
    res = await sa.spawn_worker_batch("ut_wb", ["echo 任务"], [], None, None, capture_enqueue, lambda _sid: None)
    assert "已派生" in res
    wid = sa.get_workers("ut_wb")[0]["subagent_id"]
    batch_id = sa.get_workers("ut_wb")[0]["batch_id"]
    for _ in range(100):
        await asyncio.sleep(0.05)
        if sa.get_workers("ut_wb")[0]["state"] != "running":
            break
    assert sa.get_workers("ut_wb")[0]["state"] == "done"
    assert batch_id not in sa._batches, "批次完成后应清理"
    assert got, "batch_done 应回投主 agent 队列"
    assert got[0]["type"] == "worker_batch_done"
    assert got[0]["payload"]["workers"][0]["subagent_id"] == wid
