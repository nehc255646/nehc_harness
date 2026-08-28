"""WS 集成测试 — 审批三选→执行→结果 (M1 验收路径, PLAN §8)"""

import json
import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def _recv_until(ws, event_name: str):
    """循环接收直到出现指定事件，返回该事件 payload (全局超时由 pytest-timeout 兜底)"""
    while True:
        msg = ws.receive()
        text = msg.get("text")
        if text:
            data = json.loads(text)
            if data.get("event") == event_name:
                return data["payload"]


def test_ws_approval_flow():
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        hello = _recv_until(ws, "session.hello")
        assert hello["session_id"] == session_id

        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "执行 echo hello"}})

        # heuristic 演示模式：生成 shell echo hello → 需审批
        req = _recv_until(ws, "approval.request")
        assert req["tool"] == "shell"

        ws.send_json({"event": "approval.response", "payload": {"approval_id": req["approval_id"], "decision": "approve"}})

        resolved = _recv_until(ws, "approval.resolved")
        assert resolved["approved"] is True

        result = _recv_until(ws, "tool.result")
        assert result["is_error"] is False
        assert "hello" in result["result"]


def test_ws_reconnect_keeps_pending_approval():
    """断线重连 hello 带完整 pending，审批 Future 仍可恢复（PLAN M4）。"""
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            _recv_until(ws, "session.hello")
            ws.send_json(
                {"event": "message.send", "payload": {"session_id": session_id, "content": "执行 echo hello"}}
            )
            req = _recv_until(ws, "approval.request")
            aid = req["approval_id"]

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            hello = _recv_until(ws, "session.hello")
            ids = [p["approval_id"] for p in hello.get("pending_approvals") or []]
            assert aid in ids
            assert hello["agent_state"] in ("awaiting_approval", "running")
            ws.send_json(
                {"event": "approval.response", "payload": {"approval_id": aid, "decision": "approve"}}
            )
            resolved = _recv_until(ws, "approval.resolved")
            assert resolved["approved"] is True
            result = _recv_until(ws, "tool.result")
            assert result["is_error"] is False
            assert "hello" in result["result"]


def test_ws_reconnect_keeps_session_allow_rules():
    """approve_similar 后重连，hello 带回会话放行规则。"""
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            _recv_until(ws, "session.hello")
            ws.send_json(
                {"event": "message.send", "payload": {"session_id": session_id, "content": "执行 echo hello"}}
            )
            req = _recv_until(ws, "approval.request")
            ws.send_json(
                {
                    "event": "approval.response",
                    "payload": {"approval_id": req["approval_id"], "decision": "approve_similar"},
                }
            )
            resolved = _recv_until(ws, "approval.resolved")
            assert resolved["approved"] is True
            _recv_until(ws, "tool.result")

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            hello = _recv_until(ws, "session.hello")
            rules = hello.get("session_allow_rules") or []
            assert any(r.get("kind") == "shell_prefix" and "echo" in (r.get("pattern") or "") for r in rules)


def test_ws_duplicate_approval_errors():
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        _recv_until(ws, "session.hello")
        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "执行 echo dup"}})
        req = _recv_until(ws, "approval.request")
        ws.send_json({"event": "approval.response", "payload": {"approval_id": req["approval_id"], "decision": "approve"}})
        _recv_until(ws, "approval.resolved")
        ws.send_json({"event": "approval.response", "payload": {"approval_id": req["approval_id"], "decision": "approve"}})
        err = _recv_until(ws, "error")
        assert "已处理" in err.get("message", "") or err.get("code")


def test_ws_approval_timeout(monkeypatch):
    monkeypatch.setattr(settings, "approval_timeout", 0.4)
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        _recv_until(ws, "session.hello")
        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "执行 echo timeout"}})
        _recv_until(ws, "approval.request")
        resolved = _recv_until(ws, "approval.resolved")
        assert resolved["approved"] is False
        assert resolved.get("reason") == "timeout"


def test_ws_concurrent_sessions_isolated():
    s1 = f"it_{uuid.uuid4().hex[:8]}"
    s2 = f"it_{uuid.uuid4().hex[:8]}"
    with (
        TestClient(app) as client,
        client.websocket_connect(f"/ws?session_id={s1}") as ws1,
        client.websocket_connect(f"/ws?session_id={s2}") as ws2,
    ):
        _recv_until(ws1, "session.hello")
        _recv_until(ws2, "session.hello")
        ws1.send_json({"event": "message.send", "payload": {"session_id": s1, "content": "执行 echo hold"}})
        _recv_until(ws1, "approval.request")
        ws2.send_json({"event": "message.send", "payload": {"session_id": s2, "content": "读取文件"}})
        r2 = _recv_until(ws2, "tool.result")
        assert r2.get("is_error") is False


def test_ws_write_includes_diff():
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        _recv_until(ws, "session.hello")
        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "写入 hello.txt"}})
        req = _recv_until(ws, "approval.request")
        assert req["tool"] == "write"
        ws.send_json({"event": "approval.response", "payload": {"approval_id": req["approval_id"], "decision": "approve"}})
        result = _recv_until(ws, "tool.result")
        assert result["is_error"] is False
        diff = result.get("diff") or {}
        assert "hello" in str(diff.get("new_text", "")) or "已写入" in str(result.get("result", ""))


def test_ws_agent_stop_during_shell():
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        _recv_until(ws, "session.hello")
        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "执行 sleep 20"}})
        req = _recv_until(ws, "approval.request")
        ws.send_json({"event": "approval.response", "payload": {"approval_id": req["approval_id"], "decision": "approve"}})
        _recv_until(ws, "approval.resolved")
        ws.send_json({"event": "agent.stop", "payload": {"agent_id": session_id}})
        while True:
            state = _recv_until(ws, "agent.state")
            if state.get("state") == "done":
                break


def test_ws_blacklist_blocked():
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        _recv_until(ws, "session.hello")  # hello
        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "执行 rm -rf /"}})
        result = _recv_until(ws, "tool.result")
        assert result["is_error"] is True
        assert "blocked" in result["result"]