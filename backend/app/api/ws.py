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
    """向该会话所有连接并行广播（单连接慢/死不阻塞其他连接）"""
    conns = _connections.get(session_id, set()).copy()
    if not conns:
        return
    msg = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
    results = await asyncio.gather(*(ws.send_text(msg) for ws in conns), return_exceptions=True)
    for ws, r in zip(conns, results):
        if isinstance(r, BaseException):
            logger.debug("Broadcast send failed: %s", r)


# 供 AgentLoop 回调
async def _loop_broadcaster(session_id: str, event: str, payload: dict):
    await broadcast(session_id, event, payload)


def _detach_connection(ws: WebSocket, session_id: str) -> None:
    """从会话连接表移除，空集一并清理"""
    conns = _connections.get(session_id)
    if conns and ws in conns:
        conns.remove(ws)
        if not conns:
            _connections.pop(session_id, None)


# 初始化 manager 广播器 (延迟，避免循环导入时无 loop)
def _ensure_manager_broadcaster():
    manager.set_broadcaster(_loop_broadcaster)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = ws.query_params.get("session_id", "default")
    _connections.setdefault(session_id, set()).add(ws)
    _ensure_manager_broadcaster()
    # 确保会话落库 + agent 存在并启动
    from app import persist as persist_mod

    await persist_mod.ensure_session(session_id, assign_default=True)
    agent = await manager.get_or_create(session_id)
    agent.set_broadcaster(_loop_broadcaster)
    logger.info("WS connected: session=%s total=%d state=%s", session_id, len(_connections[session_id]), agent.state)

    await _send_hello(ws, session_id, agent)

    pong_evt: asyncio.Event = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(ws, pong_evt))

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

            elif event == "pong":
                pong_evt.set()

            elif event == "message.send":
                content = payload.get("content", "")
                sid = payload.get("session_id", session_id)
                from app import persist as persist_mod

                await persist_mod.ensure_session(sid)
                target_agent = await manager.get_or_create(sid)
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
                # 先查主 agent，再查子 agent（工作型可被 agent.stop 定向终止），最后兜底当前会话主 agent
                target = manager.get(sid)
                if target:
                    await target.stop()
                else:
                    try:
                        from app.agent.subagent import stop_subagent

                        ok = await stop_subagent(sid)
                    except Exception:
                        logger.exception("Subagent stop failed")
                        ok = False
                    if not ok:
                        main = manager.get(session_id)
                        if main:
                            await main.stop()
                        else:
                            await ws.send_text(json.dumps({"event": "agent.state", "payload": {"agent_id": sid, "state": "done"}}))

            elif event == "session.create":
                new_id = str(uuid.uuid4())
                title = payload.get("title") or "New Session"
                from app import persist as persist_mod

                model_id = await persist_mod.resolve_default_model_id()
                await persist_mod.ensure_session(new_id, title=title, model_id=model_id)
                _detach_connection(ws, session_id)
                session_id = new_id
                _connections.setdefault(session_id, set()).add(ws)
                new_agent = await manager.get_or_create(session_id)
                new_agent.set_broadcaster(_loop_broadcaster)
                await _send_hello(ws, session_id, new_agent)

            elif event == "session.select":
                new_sid = payload.get("session_id", session_id)
                from app import persist as persist_mod

                await persist_mod.ensure_session(new_sid)
                _detach_connection(ws, session_id)
                session_id = new_sid
                _connections.setdefault(session_id, set()).add(ws)
                new_agent = await manager.get_or_create(session_id)
                new_agent.set_broadcaster(_loop_broadcaster)
                await _send_hello(ws, session_id, new_agent)

            elif event == "session.delete":
                sid = payload.get("session_id", session_id)
                from app import persist as persist_mod

                await manager.drop(sid)
                await persist_mod.update_session_fields(sid, status="deleted")
                await broadcast(session_id, "session.deleted", {"session_id": sid})

            elif event == "subagent.response":
                subagent_id = payload.get("subagent_id", "")
                content = payload.get("content", "")
                try:
                    from app.agent.subagent import handle_subagent_response

                    ok = await handle_subagent_response(session_id, subagent_id, content)
                    if not ok:
                        await ws.send_text(json.dumps({"event": "error", "payload": {"code": ErrorCode.INTERNAL, "message": f"subagent {subagent_id} 不存在"}}))
                except Exception as e:
                    await ws.send_text(json.dumps({"event": "error", "payload": {"code": ErrorCode.INTERNAL, "message": str(e)}}))

            else:
                logger.debug("WS unknown event: %s payload=%s", event, payload)

    except WebSocketDisconnect:
        logger.info("WS disconnected: session=%s", session_id)
        # 断连兜底：若该会话无连接，超时后自动拒绝? M1 仅记录，实时超路由由 gate 超时处理
        # 此处不立即拒绝，留给 APPROVAL_TIMEOUT
    except Exception as e:
        logger.exception("WS error")
        try:
            await ws.send_text(json.dumps({"event": "error", "payload": {"code": ErrorCode.INTERNAL, "message": str(e)}}))
        except Exception as e2:
            logger.debug("WS error send failed: %s", e2)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        _detach_connection(ws, session_id)


async def _send_hello(ws: WebSocket, session_id: str, agent):
    pending = [
        {"approval_id": p.approval_id, "tool": p.tool, "args": p.args, "reason": p.reason}
        for p in gate.list_pending(session_id)
    ]
    session_rules = gate.get_session_rules(session_id)
    try:
        from app.agent.subagent import get_panels, get_workers

        panels = get_panels(session_id)
        workers = get_workers(session_id)
    except Exception:
        panels = []
        workers = []
    title = "Session " + session_id[:8]
    model_id = None
    try:
        from app import persist as persist_mod

        row = await persist_mod.get_session(session_id)
        if row:
            title = row.title
            model_id = row.model_id
    except Exception as e:
        logger.debug("hello session lookup failed: %s", e)
    await ws.send_text(
        json.dumps(
            {
                "event": "session.hello",
                "payload": {
                    "session_id": session_id,
                    "title": title,
                    "model_id": model_id,
                    "agent_state": agent.state,
                    "pending_approvals": pending,
                    "session_allow_rules": session_rules,
                    "subagent_panels": panels,
                    "workers": workers,
                },
            },
            ensure_ascii=False,
        )
    )


async def _heartbeat(ws: WebSocket, pong_evt: asyncio.Event):
    """发送 ping 并跟踪 pong，连续 3 次未响应判死关闭 (PLAN §3)"""
    miss = 0
    try:
        while True:
            try:
                await ws.send_text(json.dumps({"event": "ping", "payload": {}}))
            except Exception:
                break
            try:
                await asyncio.wait_for(pong_evt.wait(), timeout=settings.heartbeat_interval_s)
                pong_evt.clear()
                miss = 0
            except TimeoutError:
                miss += 1
                if miss >= 3:
                    logger.info("WS heartbeat 3 misses, closing: session=%s", id(ws))
                    try:
                        await ws.close()
                    except Exception as e:
                        logger.debug("WS close failed: %s", e)
                    break
    except asyncio.CancelledError:
        pass
