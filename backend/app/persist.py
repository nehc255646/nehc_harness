"""MySQL 落库 + 内存待写队列兜底

落库点：message.done / 用户消息 / tool.result；失败入队，loop drain 时补写。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import is_available, session_scope
from app.models import (
    AppConfig,
    ChatSession,
    Message,
    Model,
    Provider,
    SubAgentRun,
    ToolLog,
    utcnow,
)

logger = logging.getLogger("neharness.persist")

_DECISION_MAP = {
    "approve": "approved_once",
    "approved_approve": "approved_once",
    "approve_similar": "approved_similar",
    "approved_approve_similar": "approved_similar",
    "reject": "rejected",
    "rejected": "rejected",
    "timeout": "timeout",
    "blocked": "blocked",
    "config_allow": "config_allow",
    "session_allow": "session_allow",
    "need_approval": "approved_once",
}


def normalize_decision(decision: str | None) -> str:
    if not decision:
        return "config_allow"
    return _DECISION_MAP.get(decision, decision)


@dataclass
class PendingWrite:
    kind: str
    payload: dict[str, Any]
    retries: int = 0


_pending: list[PendingWrite] = []
_MAX_PENDING = 500


def _enqueue_pending(kind: str, payload: dict[str, Any]) -> None:
    if len(_pending) >= _MAX_PENDING:
        logger.error("Persist pending queue full, dropping oldest")
        _pending.pop(0)
    _pending.append(PendingWrite(kind=kind, payload=payload))


def pending_count() -> int:
    return len(_pending)


# ---------- session ----------


async def ensure_session(
    session_id: str,
    title: str | None = None,
    model_id: int | None = None,
    assign_default: bool = False,
    work_mode: str | None = None,
) -> ChatSession | None:
    if not is_available():
        return None
    try:
        async with session_scope() as db:
            if db is None:
                return None
            row = await db.get(ChatSession, session_id)
            if row:
                if row.status == "deleted":
                    return None
                if title and row.title in ("New Session", f"Session {session_id[:8]}"):
                    row.title = title
                return row
            resolved = model_id
            if resolved is None and assign_default:
                resolved = await _resolve_default_model_id(db)
            row = ChatSession(
                id=session_id,
                title=title or "New Session",
                status="active",
                model_id=resolved,
                work_mode=(work_mode if work_mode in ("auto", "plan") else "auto"),
            )
            db.add(row)
            await db.flush()
            return row
    except Exception as e:
        logger.warning("ensure_session failed: %s", e)
        _enqueue_pending(
            "ensure_session",
            {
                "session_id": session_id,
                "title": title,
                "model_id": model_id,
                "assign_default": assign_default,
                "work_mode": work_mode,
            },
        )
        return None


async def get_session(session_id: str) -> ChatSession | None:
    if not is_available():
        return None
    try:
        async with session_scope() as db:
            if db is None:
                return None
            return await db.get(ChatSession, session_id)
    except Exception as e:
        logger.debug("get_session failed: %s", e)
        return None


async def list_sessions() -> list[ChatSession]:
    if not is_available():
        return []
    async with session_scope() as db:
        if db is None:
            return []
        stmt = (
            select(ChatSession)
            .where(ChatSession.status != "deleted")
            .order_by(ChatSession.updated_at.desc())
        )
        return list((await db.scalars(stmt)).all())


async def update_session_fields(session_id: str, **fields: Any) -> ChatSession | None:
    if not is_available():
        return None
    async with session_scope() as db:
        if db is None:
            return None
        row = await db.get(ChatSession, session_id)
        if not row:
            return None
        for k, v in fields.items():
            if k in ("title", "model_id", "status", "summary", "work_mode", "allow_rules") and hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = utcnow()
        await db.flush()
        return row


async def touch_session(session_id: str) -> None:
    if not is_available():
        return
    try:
        async with session_scope() as db:
            if db is None:
                return
            await db.execute(update(ChatSession).where(ChatSession.id == session_id).values(updated_at=utcnow()))
    except Exception as e:
        logger.debug("touch_session failed: %s", e)


async def save_summary(session_id: str, summary: str | None) -> None:
    await update_session_fields(session_id, summary=summary)


async def maybe_autotitle(session_id: str, content: str) -> str | None:
    """首条用户消息把占位标题换成摘要。已有自定义标题则不动。"""
    title = " ".join((content or "").strip().split())[:40]
    if not title:
        return None
    row = await get_session(session_id)
    if row is None:
        return None
    current = row.title or ""
    if current not in ("New Session", f"Session {session_id[:8]}", "") and not current.startswith("Session "):
        return None
    await update_session_fields(session_id, title=title)
    return title


# ---------- messages ----------


def history_item_to_content(item: dict) -> dict[str, Any]:
    content: dict[str, Any] = {"text": item.get("content", "") or ""}
    if item.get("tool_calls"):
        content["tool_calls"] = item["tool_calls"]
    if item.get("name"):
        content["name"] = item["name"]
    return content


def message_row_to_history(row: Message) -> dict[str, Any]:
    raw = row.content if isinstance(row.content, dict) else {"text": str(row.content)}
    item: dict[str, Any] = {
        "role": row.role,
        "content": raw.get("text", "") if isinstance(raw, dict) else str(raw),
        "public_id": row.public_id,
    }
    if isinstance(raw, dict) and raw.get("tool_calls"):
        item["tool_calls"] = raw["tool_calls"]
    if row.tool_call_id:
        item["tool_call_id"] = row.tool_call_id
    if isinstance(raw, dict) and raw.get("name"):
        item["name"] = raw["name"]
    # thinking 仅给 UI/REST，不进 loop 上下文
    return item


async def save_message(
    *,
    session_id: str,
    agent_id: str,
    role: str,
    content: str | dict,
    public_id: str | None = None,
    tool_call_id: str | None = None,
    tool_calls: list | None = None,
    name: str | None = None,
    thinking: str | None = None,
    enqueue_on_fail: bool = True,
) -> str | None:
    """message.done / 用户消息落库。返回 public_id。失败入待写队列。"""
    pid = public_id or str(uuid.uuid4())
    if isinstance(content, dict):
        payload_content = dict(content)
    else:
        payload_content = {"text": content or ""}
    if tool_calls:
        payload_content["tool_calls"] = tool_calls
    if name:
        payload_content["name"] = name
    if thinking:
        payload_content["thinking"] = thinking
    payload = {
        "session_id": session_id,
        "agent_id": agent_id,
        "role": role,
        "content": payload_content,
        "public_id": pid,
        "tool_call_id": tool_call_id,
    }
    if not is_available():
        if enqueue_on_fail:
            _enqueue_pending("message", payload)
            return pid
        raise RuntimeError("db unavailable")
    try:
        async with session_scope() as db:
            if db is None:
                if enqueue_on_fail:
                    _enqueue_pending("message", payload)
                    return pid
                raise RuntimeError("db unavailable")
            sess = await db.get(ChatSession, session_id)
            if sess is None:
                db.add(ChatSession(id=session_id, title="New Session", status="active"))
                await db.flush()
                sess = await db.get(ChatSession, session_id)
            existing = (await db.scalars(select(Message).where(Message.public_id == pid))).first()
            if existing:
                existing.content = payload_content
                if tool_call_id:
                    existing.tool_call_id = tool_call_id
            else:
                db.add(
                    Message(
                        public_id=pid,
                        session_id=session_id,
                        agent_id=agent_id,
                        role=role,
                        content=payload_content,
                        tool_call_id=tool_call_id,
                    )
                )
            sess = await db.get(ChatSession, session_id)
            if sess:
                sess.updated_at = utcnow()
                if role == "user" and sess.title in ("New Session", f"Session {session_id[:8]}"):
                    text = payload_content.get("text") or ""
                    if text and not text.startswith("["):
                        sess.title = text[:40]
        return pid
    except Exception as e:
        logger.warning("save_message failed: %s", e)
        if enqueue_on_fail:
            _enqueue_pending("message", payload)
            return pid
        raise


async def save_tool_log(
    *,
    session_id: str,
    agent_id: str,
    name: str,
    args: dict,
    result: Any,
    tool_call_id: str,
    is_error: bool = False,
    duration_ms: int | None = None,
    decision: str | None = None,
    rule_hit: str | None = None,
    message_public_id: str | None = None,
    enqueue_on_fail: bool = True,
) -> None:
    payload = {
        "session_id": session_id,
        "agent_id": agent_id,
        "name": name,
        "args": args,
        "result": result if isinstance(result, (dict, list)) else {"text": str(result)},
        "tool_call_id": tool_call_id,
        "is_error": is_error,
        "duration_ms": duration_ms,
        "decision": normalize_decision(decision),
        "rule_hit": (rule_hit or "")[:256] or None,
        "message_public_id": message_public_id,
    }
    if not is_available():
        if enqueue_on_fail:
            _enqueue_pending("tool_log", payload)
            return
        raise RuntimeError("db unavailable")
    try:
        await ensure_session(session_id)
        async with session_scope() as db:
            if db is None:
                if enqueue_on_fail:
                    _enqueue_pending("tool_log", payload)
                    return
                raise RuntimeError("db unavailable")
            message_pk = None
            if message_public_id:
                msg = (await db.scalars(select(Message).where(Message.public_id == message_public_id))).first()
                if msg:
                    message_pk = msg.id
            db.add(
                ToolLog(
                    session_id=session_id,
                    message_id=message_pk,
                    tool_call_id=tool_call_id,
                    agent_id=agent_id,
                    name=name,
                    args=payload["args"],
                    result=payload["result"],
                    is_error=is_error,
                    duration_ms=duration_ms,
                    rule_hit=payload["rule_hit"],
                    decision=payload["decision"],
                )
            )
    except Exception as e:
        logger.warning("save_tool_log failed: %s", e)
        if enqueue_on_fail:
            _enqueue_pending("tool_log", payload)
            return
        raise


async def upsert_subagent_run(
    *,
    main_session_id: str,
    subagent_id: str,
    kind: str,
    behavior_desc: str | None = None,
    goal: str | None = None,
    status: str = "running",
    result: str | None = None,
    late: bool = False,
    finished: bool = False,
    enqueue_on_fail: bool = True,
) -> None:
    payload = {
        "main_session_id": main_session_id,
        "subagent_id": subagent_id,
        "kind": kind,
        "behavior_desc": behavior_desc,
        "goal": goal,
        "status": status,
        "result": result,
        "late": late,
        "finished": finished,
    }
    if not is_available():
        if enqueue_on_fail:
            _enqueue_pending("subagent_run", payload)
            return
        raise RuntimeError("db unavailable")
    try:
        await ensure_session(main_session_id)
        async with session_scope() as db:
            if db is None:
                if enqueue_on_fail:
                    _enqueue_pending("subagent_run", payload)
                    return
                raise RuntimeError("db unavailable")
            row = (await db.scalars(select(SubAgentRun).where(SubAgentRun.subagent_id == subagent_id))).first()
            if row is None:
                row = SubAgentRun(
                    main_session_id=main_session_id,
                    subagent_id=subagent_id,
                    kind=kind,
                    behavior_desc=behavior_desc,
                    goal=goal,
                    status=status,
                    result=result,
                    late=late,
                )
                db.add(row)
            else:
                row.status = status
                if result is not None:
                    row.result = result
                row.late = late
                if behavior_desc is not None:
                    row.behavior_desc = behavior_desc
                if goal is not None:
                    row.goal = goal
            if finished:
                row.finished_at = utcnow()
    except Exception as e:
        logger.warning("upsert_subagent_run failed: %s", e)
        if enqueue_on_fail:
            _enqueue_pending("subagent_run", payload)
            return
        raise


async def load_history(session_id: str) -> tuple[list[dict], str | None, int | None]:
    """返回 (history, summary, model_id)，供重启后重建 loop 上下文。"""
    if not is_available():
        return [], None, None
    try:
        async with session_scope() as db:
            if db is None:
                return [], None, None
            sess = await db.get(ChatSession, session_id)
            if not sess or sess.status == "deleted":
                return [], None, None
            rows = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(Message.session_id == session_id, Message.agent_id == "main")
                        .order_by(Message.id.asc())
                    )
                ).all()
            )
            history = [message_row_to_history(r) for r in rows]
            return history, sess.summary, sess.model_id
    except Exception as e:
        logger.warning("load_history failed: %s", e)
        return [], None, None


async def load_late_subagent_results(session_id: str) -> list[SubAgentRun]:
    """迟到未喂回的子 agent 结果（主 agent done 期间完成），续聊时喂回上下文。"""
    if not is_available():
        return []
    try:
        async with session_scope() as db:
            if db is None:
                return []
            return list(
                (
                    await db.scalars(
                        select(SubAgentRun)
                        .where(
                            SubAgentRun.main_session_id == session_id,
                            SubAgentRun.late.is_(True),
                            SubAgentRun.late_fed_back.is_(False),
                            SubAgentRun.result.is_not(None),
                            SubAgentRun.status.in_(("done", "error")),
                        )
                        .order_by(SubAgentRun.id.asc())
                    )
                ).all()
            )
    except Exception as e:
        logger.warning("load_late_subagent_results failed: %s", e)
        return []


async def mark_subagent_fed_back(subagent_id: str) -> None:
    if not is_available():
        return
    try:
        async with session_scope() as db:
            if db is None:
                return
            await db.execute(
                update(SubAgentRun).where(SubAgentRun.subagent_id == subagent_id).values(late_fed_back=True)
            )
    except Exception as e:
        logger.debug("mark_subagent_fed_back failed: %s", e)


async def list_messages(session_id: str, agent_id: str | None = "main") -> list[Message]:
    if not is_available():
        return []
    async with session_scope() as db:
        if db is None:
            return []
        stmt = select(Message).where(Message.session_id == session_id)
        if agent_id is not None:
            stmt = stmt.where(Message.agent_id == agent_id)
        return list((await db.scalars(stmt.order_by(Message.id.asc()))).all())


def _clean_allow_rules(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        pattern = str(item.get("pattern") or "").strip()
        if kind in ("shell_prefix", "tool") and pattern:
            out.append({"kind": kind, "pattern": pattern})
    return out


async def list_subagent_runs(session_id: str, kind: str | None = None) -> list[SubAgentRun]:
    if not is_available():
        return []
    try:
        async with session_scope() as db:
            if db is None:
                return []
            stmt = select(SubAgentRun).where(SubAgentRun.main_session_id == session_id)
            if kind:
                stmt = stmt.where(SubAgentRun.kind == kind)
            return list((await db.scalars(stmt.order_by(SubAgentRun.id.asc()))).all())
    except Exception as e:
        logger.debug("list_subagent_runs failed: %s", e)
        return []


def message_row_to_panel(row: Message) -> dict | None:
    if row.role not in ("user", "assistant"):
        return None
    raw = row.content if isinstance(row.content, dict) else {"text": str(row.content)}
    text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
    if not str(text or "").strip():
        return None
    return {"id": row.public_id, "role": row.role, "content": str(text)}


async def load_session_allow_rules(session_id: str) -> list[dict]:
    row = await get_session(session_id)
    if row is None:
        return []
    return _clean_allow_rules(getattr(row, "allow_rules", None))


async def save_session_allow_rules(session_id: str, rules: list[dict]) -> None:
    await update_session_fields(session_id, allow_rules=_clean_allow_rules(rules))


async def get_tool_log(session_id: str, tool_call_id: str) -> ToolLog | None:
    if not is_available():
        return None
    async with session_scope() as db:
        if db is None:
            return None
        return (
            await db.scalars(
                select(ToolLog)
                .where(ToolLog.session_id == session_id, ToolLog.tool_call_id == tool_call_id)
                .order_by(ToolLog.id.desc())
            )
        ).first()


async def list_tool_logs(session_id: str) -> list[ToolLog]:
    if not is_available():
        return []
    async with session_scope() as db:
        if db is None:
            return []
        return list(
            (await db.scalars(select(ToolLog).where(ToolLog.session_id == session_id).order_by(ToolLog.id.asc()))).all()
        )


# ---------- model resolution ----------


async def _resolve_default_model_id(db: AsyncSession) -> int | None:
    last = (
        await db.scalars(
            select(ChatSession)
            .where(ChatSession.status != "deleted", ChatSession.model_id.is_not(None))
            .order_by(ChatSession.updated_at.desc())
            .limit(1)
        )
    ).first()
    if last and last.model_id:
        model = await db.get(Model, last.model_id)
        if model:
            return last.model_id
    cfg = (await db.scalars(select(AppConfig).where(AppConfig.key == "default_model_id"))).first()
    if cfg and cfg.value:
        try:
            mid = int(cfg.value)
        except ValueError:
            return None
        model = await db.get(Model, mid)
        if model:
            return mid
    return None


async def resolve_default_model_id() -> int | None:
    if not is_available():
        return None
    async with session_scope() as db:
        if db is None:
            return None
        return await _resolve_default_model_id(db)


async def count_models() -> int:
    if not is_available():
        return 0
    async with session_scope() as db:
        if db is None:
            return 0
        from sqlalchemy import func as sa_func

        return int(await db.scalar(select(sa_func.count()).select_from(Model)) or 0)


async def count_providers() -> int:
    if not is_available():
        return 0
    async with session_scope() as db:
        if db is None:
            return 0
        from sqlalchemy import func as sa_func

        return int(await db.scalar(select(sa_func.count()).select_from(Provider)) or 0)


# ---------- pending flush ----------


async def flush_pending() -> int:
    """loop drain 时补写失败队列。返回成功条数。"""
    if not _pending or not is_available():
        return 0
    items = list(_pending)
    _pending.clear()
    ok = 0
    for item in items:
        try:
            if item.kind == "message":
                await save_message(
                    **{
                        k: item.payload[k]
                        for k in item.payload
                        if k in ("session_id", "agent_id", "role", "content", "public_id", "tool_call_id")
                    },
                    enqueue_on_fail=False,
                )
            elif item.kind == "tool_log":
                await save_tool_log(
                    session_id=item.payload["session_id"],
                    agent_id=item.payload["agent_id"],
                    name=item.payload["name"],
                    args=item.payload["args"],
                    result=item.payload["result"],
                    tool_call_id=item.payload["tool_call_id"],
                    is_error=item.payload.get("is_error", False),
                    duration_ms=item.payload.get("duration_ms"),
                    decision=item.payload.get("decision"),
                    rule_hit=item.payload.get("rule_hit"),
                    message_public_id=item.payload.get("message_public_id"),
                    enqueue_on_fail=False,
                )
            elif item.kind == "subagent_run":
                await upsert_subagent_run(
                    main_session_id=item.payload["main_session_id"],
                    subagent_id=item.payload["subagent_id"],
                    kind=item.payload["kind"],
                    behavior_desc=item.payload.get("behavior_desc"),
                    goal=item.payload.get("goal"),
                    status=item.payload.get("status", "running"),
                    result=item.payload.get("result"),
                    late=item.payload.get("late", False),
                    finished=item.payload.get("finished", False),
                    enqueue_on_fail=False,
                )
            elif item.kind == "ensure_session":
                await ensure_session(
                    item.payload["session_id"],
                    title=item.payload.get("title"),
                    model_id=item.payload.get("model_id"),
                    assign_default=item.payload.get("assign_default", False),
                    work_mode=item.payload.get("work_mode"),
                )
            ok += 1
        except Exception as e:
            item.retries += 1
            if item.retries < 5:
                _pending.append(item)
            logger.warning("flush_pending item failed (%s): %s", item.kind, e)
    return ok


# ---------- env bootstrap ----------


async def maybe_import_env_provider() -> None:
    """库为空时从 OPENAI_* env 导入一组 Provider/Model，不自动设兜底。"""
    from app.core.config import settings
    from app.core.crypto import encrypt_secret, encryption_ready

    if not is_available():
        return
    if await count_providers() > 0:
        return
    if not settings.openai_api_key or not settings.openai_base_url:
        return
    if not encryption_ready():
        logger.warning("跳过 env 导入 Provider：ENCRYPTION_KEY 未就绪")
        return
    try:
        async with session_scope() as db:
            if db is None:
                return
            p = Provider(
                provider_id="env",
                display_name="Env (imported)",
                base_url=settings.openai_base_url.rstrip("/"),
                api_key_encrypted=encrypt_secret(settings.openai_api_key),
            )
            db.add(p)
            await db.flush()
            db.add(
                Model(
                    provider_id=p.id,
                    model_id=settings.openai_model or "gpt-4o-mini",
                    display_name=settings.openai_model or "gpt-4o-mini",
                    context_window=128000,
                    temperature=0.2,
                )
            )
            logger.info("Imported Provider/Model from OPENAI_* env (未设兜底模型)")
    except Exception as e:
        logger.warning("env 导入 Provider 失败: %s", e)
