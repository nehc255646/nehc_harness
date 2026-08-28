"""M3 持久化：落库 / 待写队列 / 历史重建"""

import uuid

import pytest

from app.core.crypto import decrypt_secret, encrypt_secret, encryption_ready
from app.core.db import init_db
from app.persist import (
    ensure_session,
    flush_pending,
    load_history,
    pending_count,
    save_message,
    save_tool_log,
)


@pytest.fixture
async def db_ready():
    ok = await init_db()
    if not ok:
        pytest.skip("MySQL unavailable")
    return True


async def test_encrypt_roundtrip():
    if not encryption_ready():
        pytest.skip("ENCRYPTION_KEY missing")
    token = encrypt_secret("sk-test-secret")
    assert token != "sk-test-secret"
    assert decrypt_secret(token) == "sk-test-secret"


async def test_save_and_load_history(db_ready):
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="persist-test")
    uid = await save_message(session_id=sid, agent_id="main", role="user", content="hello persist")
    aid = await save_message(session_id=sid, agent_id="main", role="assistant", content="hi there")
    assert uid and aid
    hist, summary, _mid = await load_history(sid)
    texts = [m["content"] for m in hist]
    assert "hello persist" in texts
    assert "hi there" in texts
    assert summary is None


async def test_tool_log_and_assistant_tool_calls(db_ready):
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="tools")
    pid = str(uuid.uuid4())
    await save_message(
        session_id=sid,
        agent_id="main",
        role="assistant",
        content="run",
        public_id=pid,
        tool_calls=[{"name": "shell", "args": {"command": "echo x"}, "id": "c1"}],
    )
    await save_tool_log(
        session_id=sid,
        agent_id="main",
        name="shell",
        args={"command": "echo x"},
        result="x",
        tool_call_id="c1",
        is_error=False,
        duration_ms=12,
        decision="approved_once",
        message_public_id=pid,
    )
    hist, _, _ = await load_history(sid)
    assert hist[-1]["tool_calls"][0]["name"] == "shell"


async def test_flush_pending_noop_when_empty(db_ready):
    n = pending_count()
    flushed = await flush_pending()
    assert flushed >= 0
    assert pending_count() <= n
