"""M3: 从 DB 重建 AgentLoop 上下文（模拟重启）"""

import asyncio
import uuid

from app.agent.loop import AgentLoop
from app.core.config import settings
from app.core.db import init_db
from app.persist import ensure_session, save_message


async def test_hydrate_rebuilds_history():
    ok = await init_db()
    if not ok:
        import pytest

        pytest.skip("MySQL unavailable")
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="hydrate")
    await save_message(session_id=sid, agent_id="main", role="user", content="任务A")
    await save_message(session_id=sid, agent_id="main", role="assistant", content="已收到")

    agent = AgentLoop(sid)
    assert agent.history == []
    await agent.hydrate_from_db()
    assert [m["content"] for m in agent.history] == ["任务A", "已收到"]


async def test_hydrate_then_start_does_not_call_model():
    """有历史的会话 start 后保持 idle，直到 enqueue 新消息才调模型。"""
    ag = AgentLoop("ut_noauto")
    ag.history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "done"}]
    called: list = []

    async def fake_call(_messages):
        called.append(1)
        return {"text": "should-not", "tool_calls": [], "streamed": False}

    ag._call_model = fake_call  # type: ignore[method-assign]
    await ag.start()
    await asyncio.sleep(0.15)
    assert called == []
    assert ag.state == "idle"
    await ag.enqueue({"type": "user_message", "content": "hi"})
    for _ in range(80):
        await asyncio.sleep(0.02)
        if called:
            break
    assert called, "新用户消息后应调模型"
    await ag.stop()


async def test_hydrate_applies_window_slice():
    ok = await init_db()
    if not ok:
        import pytest

        pytest.skip("MySQL unavailable")
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="window")
    keep = settings.window_n * 2
    for i in range(keep + 4):
        role = "user" if i % 2 == 0 else "assistant"
        await save_message(session_id=sid, agent_id="main", role=role, content=f"m{i}")
    agent = AgentLoop(sid)
    await agent.hydrate_from_db()
    assert len(agent.history) == keep
    assert agent.history[0]["content"] == "m4"
