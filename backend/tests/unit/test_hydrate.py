"""M3: 从 DB 重建 AgentLoop 上下文（模拟重启）"""

import uuid

from app.agent.loop import AgentLoop
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
