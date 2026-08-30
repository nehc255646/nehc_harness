"""M3 持久化：落库 / 待写队列 / 历史重建"""

import uuid

import pytest

from app.core.crypto import decrypt_secret, encrypt_secret, encryption_ready
from app.core.db import init_db
from app.persist import (
    ensure_session,
    flush_pending,
    get_session,
    get_tool_log,
    load_history,
    maybe_autotitle,
    pending_count,
    save_message,
    save_tool_log,
    update_session_fields,
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
    assert decrypt_secret("") == ""


def test_env_api_key(monkeypatch):
    from app.core.crypto import env_api_key, provider_api_key

    monkeypatch.setenv("MY_CUSTOM_KEY", "from-name")
    assert env_api_key("MY_CUSTOM_KEY") == "from-name"
    assert env_api_key("") == ""
    assert env_api_key("MISSING_VAR_XYZ") == ""

    class P:
        provider_id = "opencode_zen"
        api_key_from_env = True
        api_key_env = "MY_CUSTOM_KEY"
        api_key_encrypted = ""

    assert provider_api_key(P()) == "from-name"

    class Direct:
        api_key_from_env = False
        api_key_env = None
        api_key_encrypted = ""

    assert provider_api_key(Direct()) == ""


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


async def test_thinking_persisted_but_not_in_loop_history(db_ready):
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="think")
    await save_message(
        session_id=sid,
        agent_id="main",
        role="assistant",
        content="答案",
        thinking="我先想一步",
    )
    hist, _, _ = await load_history(sid)
    assert hist[-1]["content"] == "答案"
    assert "thinking" not in hist[-1]
    from app.persist import list_messages

    rows = await list_messages(sid)
    raw = rows[-1].content
    assert raw["text"] == "答案"
    assert raw["thinking"] == "我先想一步"


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
    log = await get_tool_log(sid, "c1")
    assert log is not None
    assert log.message_id is not None
    assert log.decision == "approved_once"


async def test_maybe_autotitle_only_placeholder(db_ready):
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="New Session")
    t = await maybe_autotitle(sid, "执行 echo hello world")
    assert t == "执行 echo hello world"
    row = await get_session(sid)
    assert row and row.title == "执行 echo hello world"
    t2 = await maybe_autotitle(sid, "第二句不应覆盖")
    assert t2 is None
    row2 = await get_session(sid)
    assert row2 and row2.title == "执行 echo hello world"


async def test_tool_log_keeps_full_text_and_diff(db_ready):
    sid = f"ut_{uuid.uuid4().hex[:12]}"
    await ensure_session(sid, title="diff-log")
    blob = "X" * 2000
    await save_tool_log(
        session_id=sid,
        agent_id="main",
        name="write",
        args={"path": "a.txt", "content": blob},
        result={"text": blob, "diff": {"path": "a.txt", "old_text": "", "new_text": blob}},
        tool_call_id="c_diff",
        is_error=False,
        decision="approved_once",
    )
    log = await get_tool_log(sid, "c_diff")
    assert log is not None
    assert isinstance(log.result, dict)
    assert log.result["text"] == blob
    assert log.result["diff"]["new_text"] == blob


async def test_flush_pending_increments_retries(monkeypatch):
    from app import persist as persist_mod

    persist_mod._pending.clear()
    persist_mod._enqueue_pending(
        "message",
        {
            "session_id": "ut_retry",
            "agent_id": "main",
            "role": "user",
            "content": {"text": "x"},
            "public_id": str(uuid.uuid4()),
            "tool_call_id": None,
        },
    )

    async def boom(**_kwargs):
        raise RuntimeError("forced fail")

    monkeypatch.setattr(persist_mod, "is_available", lambda: True)
    monkeypatch.setattr(persist_mod, "save_message", boom)
    flushed = await persist_mod.flush_pending()
    assert flushed == 0
    assert persist_mod.pending_count() == 1
    assert persist_mod._pending[0].retries == 1
    persist_mod._pending.clear()


async def test_flush_pending_drops_after_five(monkeypatch):
    from app import persist as persist_mod

    persist_mod._pending.clear()
    persist_mod._enqueue_pending(
        "message",
        {
            "session_id": "ut_drop",
            "agent_id": "main",
            "role": "user",
            "content": {"text": "x"},
            "public_id": str(uuid.uuid4()),
            "tool_call_id": None,
        },
    )
    persist_mod._pending[0].retries = 4

    async def boom(**_kwargs):
        raise RuntimeError("forced fail")

    monkeypatch.setattr(persist_mod, "is_available", lambda: True)
    monkeypatch.setattr(persist_mod, "save_message", boom)
    await persist_mod.flush_pending()
    assert persist_mod.pending_count() == 0


async def test_ensure_session_skips_deleted(db_ready):
    sid = f"del_{uuid.uuid4().hex[:12]}"
    row = await ensure_session(sid, title="to-delete")
    assert row is not None
    await update_session_fields(sid, status="deleted")
    again = await ensure_session(sid, title="resurrect")
    assert again is None
    hist, _, _ = await load_history(sid)
    assert hist == []


async def test_flush_pending_noop_when_empty(db_ready):
    n = pending_count()
    flushed = await flush_pending()
    assert flushed >= 0
    assert pending_count() <= n
