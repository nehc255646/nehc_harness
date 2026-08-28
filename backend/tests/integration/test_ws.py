"""WS 集成测试 — 审批三选→执行→结果 (M1 验收路径, PLAN §8)"""

import json
import uuid

from fastapi.testclient import TestClient

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


def test_ws_blacklist_blocked():
    session_id = f"it_{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client, client.websocket_connect(f"/ws?session_id={session_id}") as ws:
        _recv_until(ws, "session.hello")  # hello
        ws.send_json({"event": "message.send", "payload": {"session_id": session_id, "content": "执行 rm -rf /"}})
        result = _recv_until(ws, "tool.result")
        assert result["is_error"] is True
        assert "blocked" in result["result"]