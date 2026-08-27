"""WebSocket 端点 — 对应 PLAN.md §3 协议 + M1 完整版"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.manager import manager
from app.core.config import settings
from app.core.errors import ErrorCode
from app.permissions.gate import gate

logger = logging.getLogger("harness.ws")

router = APIRouter()

# 活跃连接：session_id -> set[WebSocket]
_connections: dict[str, set[WebSocket]] = {}


async def broadcast(session_id: str, event: str, payload: dict):
    """向该会话所有连接广播"""
    conns = _connections.get(session_id, set()).copy()
    msg = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
    for ws in conns:
        try:
            await ws.send_text(msg)
        except Exception:
            pass


# 供 AgentLoop 回调
async def _loop_broadcaster(session_id: str, event: str, payload: dict):
    await broadcast(session_id, event, payload)


# 初始化 manager 广播器 (延迟，避免循环导入时无 loop)
def _ensure_manager_broadcaster():
    manager.set_broadcaster(_loop_broadcaster)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = ws.query_params.get("session_id", "default")
    _connections.setdefault(session_id, set()).add(ws)
    _ensure_manager_broadcaster()
    # 确保 agent 存在并启动
    agent = manager.get_or_create(session_id)
    agent.set_broadcaster(_loop_broadcaster)
    logger.info("WS connected: session=%s total=%d state=%s", session_id, len(_connections[session_id]), agent.state)

    # hello 握手 — 对应 PLAN §2.5 断线恢复
    pending = [
        {"approval_id": p.approval_id, "tool": p.tool, "args": p.args, "reason": p.reason}
        for p in gate.list_pending(session_id)
    ]
    session_rules = gate.get_session_rules(session_id)
    await ws.send_text(
        json.dumps(
            {
                "event": "session.hello",
                "payload": {
                    "session_id": session_id,
                    "title": "Session " + session_id[:8],
                    "agent_state": agent.state,
                    "pending_approvals": pending,
                    "session_allow_rules": session_rules,
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
                await ws.send_text(json.dumps({"event": "error", "payload": {"code": ErrorCode.INTERNAL, "message": "invalid json"}}))
                continue

            event = data.get("event")
            payload = data.get("payload", {})

            if event == "ping":
                await ws.send_text(json.dumps({"event": "pong", "payload": {}}))

            elif event == "message.send":
                content = payload.get("content", "")
                # 适配前端 payload 含 session_id
                sid = payload.get("session_id", session_id)
                target_agent = manager.get_or_create(sid)
                target_agent.set_broadcaster(_loop_broadcaster)
                await target_agent.enqueue({"type": "user_message", "content": content})
                # 本次发送立即广播用户消息的落库事件 (可选，前端已本地渲染)
                # loop 会在下一轮广播 assistant 流式

            elif event == "approval.response":
                approval_id = payload.get("approval_id")
                decision = payload.get("decision")  # approve | approve_similar | reject
                # 归一化
                if decision == "approve":
                    d = "approve"
                elif decision == "approve_similar":
                    d = "approve_similar"
                else:
                    d = "reject"
                ok = gate.resolve(approval_id, d, reason="user")
                if not ok:
                    await ws.send_text(json.dumps({"event": "error", "payload": {"code": ErrorCode.INTERNAL, "message": f"approval {approval_id} 已处理或不存在"}}))
                else:
                    # 成功消费后，向该会话广播 resolved (loop 内也会广播，此为兜底)
                    # 由 gate 的 Future 唤醒 loop，loop 会广播 approval.resolved
                    pass
                # 同步更新 hello 中的 session_allow_rules
                # 广播最新规则
                rules = gate.get_session_rules(session_id)
                await broadcast(session_id, "session.update", {"session_allow_rules": rules})

            elif event == "agent.stop":
                sid = payload.get("agent_id", session_id) if isinstance(payload.get("agent_id"), str) else session_id
                # agent_id 在 M1 即 session_id
                target = manager.get(sid) or manager.get(session_id)
                if target:
                    await target.stop()
                else:
                    await ws.send_text(json.dumps({"event": "agent.state", "payload": {"agent_id": sid, "state": "done"}}))

            elif event == "session.create":
                new_id = str(uuid.uuid4())
                await ws.send_text(
                    json.dumps(
                        {
                            "event": "session.hello",
                            "payload": {
                                "session_id": new_id,
                                "title": payload.get("title", "New Session"),
                                "agent_state": "idle",
                                "pending_approvals": [],
                                "session_allow_rules": [],
                                "subagent_panels": [],
                            },
                        },
                        ensure_ascii=False,
                    )
                )

            elif event == "session.select":
                # 切换会话 — 重新握手
                new_sid = payload.get("session_id", session_id)
                # 将当前连接迁移
                old_conns = _connections.get(session_id)
                if old_conns and ws in old_conns:
                    old_conns.remove(ws)
                session_id = new_sid
                _connections.setdefault(session_id, set()).add(ws)
                new_agent = manager.get_or_create(session_id)
                new_agent.set_broadcaster(_loop_broadcaster)
                pending = [
                    {"approval_id": p.approval_id, "tool": p.tool, "args": p.args, "reason": p.reason}
                    for p in gate.list_pending(session_id)
                ]
                rules = gate.get_session_rules(session_id)
                await ws.send_text(
                    json.dumps(
                        {
                            "event": "session.hello",
                            "payload": {
                                "session_id": session_id,
                                "title": "Session " + session_id[:8],
                                "agent_state": new_agent.state,
                                "pending_approvals": pending,
                                "session_allow_rules": rules,
                                "subagent_panels": [],
                            },
                        },
                        ensure_ascii=False,
                    )
                )

            else:
                logger.debug("WS unknown event: %s payload=%s", event, payload)

    except WebSocketDisconnect:
        logger.info("WS disconnected: session=%s", session_id)
        # 断连兜底：若该会话无连接，超时后自动拒绝? M1 仅记录，实时超路由由 gate 超时处理
        # 此处不立即拒绝，留给 APPROVAL_TIMEOUT
    except Exception as e:
        logger.exception("WS error: %s", e)
        try:
            await ws.send_text(json.dumps({"event": "error", "payload": {"code": ErrorCode.INTERNAL, "message": str(e)}}))
        except Exception:
            pass
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
                # 延迟拒绝 pending? 保留超时机制，不立即清理


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
