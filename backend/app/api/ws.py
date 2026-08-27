"""WebSocket 端点 — 极简打通 + 心跳占位 (M1)"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings

logger = logging.getLogger("harness.ws")

router = APIRouter()

# 活跃连接：session_id -> set[WebSocket]
_connections: dict[str, set[WebSocket]] = {}


async def broadcast(session_id: str, event: str, payload: dict):
    conns = _connections.get(session_id, set()).copy()
    msg = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
    for ws in conns:
        try:
            await ws.send_text(msg)
        except Exception:
            pass


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = ws.query_params.get("session_id", "default")
    _connections.setdefault(session_id, set()).add(ws)
    logger.info("WS connected: session=%s total=%d", session_id, len(_connections[session_id]))

    # hello 握手
    await ws.send_text(
        json.dumps(
            {
                "event": "session.hello",
                "payload": {
                    "session_id": session_id,
                    "title": "New Session",
                    "agent_state": "idle",
                    "pending_approvals": [],
                    "session_allow_rules": [],
                    "subagent_panels": [],
                },
            },
            ensure_ascii=False,
        )
    )

    heartbeat_task = asyncio.create_task(_heartbeat(ws))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"event": "error", "payload": {"code": "INTERNAL", "message": "invalid json"}}))
                continue

            event = data.get("event")
            payload = data.get("payload", {})

            if event == "ping":
                await ws.send_text(json.dumps({"event": "pong", "payload": {}}))

            elif event == "message.send":
                content = payload.get("content", "")
                msg_id = str(uuid.uuid4())
                # 极简回显 — 后续由 agent/loop.py 接管
                await ws.send_text(json.dumps({"event": "message.start", "payload": {"agent_id": "main", "message_id": msg_id, "role": "assistant"}}))
                await ws.send_text(json.dumps({"event": "message.delta", "payload": {"agent_id": "main", "message_id": msg_id, "delta": f"收到: {content} (M1 loop 尚未接入，此为占位回显)"}}))
                await ws.send_text(json.dumps({"event": "message.done", "payload": {"message_id": msg_id, "role": "assistant", "content": f"收到: {content}"}}))
                await ws.send_text(json.dumps({"event": "agent.state", "payload": {"agent_id": "main", "state": "idle"}}))

            elif event == "approval.response":
                await ws.send_text(json.dumps({"event": "approval.resolved", "payload": {"approval_id": payload.get("approval_id"), "approved": payload.get("decision") != "reject", "reason": "user"}}))

            elif event == "agent.stop":
                await ws.send_text(json.dumps({"event": "agent.state", "payload": {"agent_id": payload.get("agent_id", "main"), "state": "done"}}))

            elif event == "session.create":
                new_id = str(uuid.uuid4())
                await ws.send_text(json.dumps({"event": "session.hello", "payload": {"session_id": new_id, "title": payload.get("title", "New Session"), "agent_state": "idle", "pending_approvals": [], "session_allow_rules": [], "subagent_panels": []}}))

            else:
                logger.debug("WS unknown event: %s", event)

    except WebSocketDisconnect:
        logger.info("WS disconnected: session=%s", session_id)
    except Exception as e:
        logger.exception("WS error: %s", e)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        conns = _connections.get(session_id)
        if conns and ws in conns:
            conns.remove(ws)
            if not conns:
                _connections.pop(session_id, None)


async def _heartbeat(ws: WebSocket):
    try:
        while True:
            await asyncio.sleep(settings.heartbeat_interval_s)
            try:
                await ws.send_text(json.dumps({"event": "ping", "payload": {}}))
            except Exception:
                break
    except asyncio.CancelledError:
        pass
