"""REST API — 会话/历史/Provider·Model 配置/审批详情 (M3)"""

from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.crypto import encrypt_secret, encryption_ready
from app.core.db import is_available, session_scope
from app.models import AppConfig, ChatSession, Model, Provider, SubAgentRun, ToolLog, utcnow
from app.permissions.gate import gate
from app.persist import (
    count_models,
    ensure_session,
    list_messages,
    list_sessions,
    list_tool_logs,
    resolve_default_model_id,
)
from app.schemas import (
    DefaultModelBody,
    LlmProbeBody,
    ModelCreate,
    ModelOut,
    ModelUpdate,
    ProviderCreate,
    ProviderOut,
    ProviderTestBody,
    ProviderUpdate,
    SessionCreate,
    SessionOut,
    SessionUpdate,
    ToolLogOut,
)

logger = logging.getLogger("harness.rest")
router = APIRouter(prefix="/api", tags=["rest"])


def _require_db() -> None:
    if not is_available():
        raise HTTPException(status_code=503, detail="MySQL 不可用")


def _provider_out(p: Provider) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        provider_id=p.provider_id,
        display_name=p.display_name,
        base_url=p.base_url,
        api_key_set=bool(p.api_key_encrypted),
        api_key_from_env=bool(p.api_key_from_env),
        api_key_env=p.api_key_env,
        created_at=p.created_at,
    )


def _encrypt_api_key(plain: str) -> str:
    text = (plain or "").strip()
    if not text:
        return ""
    if not encryption_ready():
        raise HTTPException(status_code=400, detail="ENCRYPTION_KEY 未配置或非法")
    return encrypt_secret(text)


async def _probe_chat(base_url: str, api_key: str, model_name: str) -> dict:
    """对指定模型发一条 hello，失败也返回 200 + ok=false。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "缺少 base_url", "model": model_name}
    url = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "error": resp.text[:500], "model": model_name}
        data = resp.json()
        text = ""
        try:
            text = data["choices"][0]["message"]["content"]
        except Exception:
            text = str(data)[:200]
        return {"ok": True, "model": model_name, "reply": text}
    except Exception as e:
        return {"ok": False, "error": str(e) or type(e).__name__, "model": model_name}


def _model_out(m: Model) -> ModelOut:
    slug = m.provider.provider_id if m.provider else None
    name = m.provider.display_name if m.provider else None
    return ModelOut(
        id=m.id,
        provider_id=m.provider_id,
        provider_slug=slug,
        provider_name=name,
        model_id=m.model_id,
        display_name=m.display_name,
        context_window=m.context_window,
        temperature=m.temperature,
        created_at=m.created_at,
    )


@router.get("/health")
async def health():
    return {"status": "ok", "mysql": is_available()}


# ---------- sessions ----------


@router.get("/sessions", response_model=list[SessionOut])
async def api_list_sessions():
    _require_db()
    rows = await list_sessions()
    return [SessionOut.model_validate(r) for r in rows]


@router.post("/sessions", response_model=SessionOut)
async def api_create_session(body: SessionCreate):
    _require_db()
    sid = str(uuid.uuid4())
    model_id = body.model_id
    if model_id is None:
        model_id = await resolve_default_model_id()
    row = await ensure_session(
        sid,
        title=body.title or "New Session",
        model_id=model_id,
        assign_default=False,
        work_mode=body.work_mode,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="创建会话失败")
    from app.agent.manager import manager

    await manager.get_or_create(sid)
    return SessionOut.model_validate(row)


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def api_get_session(session_id: str):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        row = await db.get(ChatSession, session_id)
        if not row or row.status == "deleted":
            raise HTTPException(status_code=404, detail="session not found")
        return SessionOut.model_validate(row)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def api_patch_session(session_id: str, body: SessionUpdate):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        row = await db.get(ChatSession, session_id)
        if not row or row.status == "deleted":
            raise HTTPException(status_code=404, detail="session not found")
        if body.title is not None:
            row.title = body.title
        if body.status is not None:
            if body.status not in ("active", "archived", "deleted"):
                raise HTTPException(status_code=400, detail="非法 status")
            row.status = body.status
        dumped = body.model_dump(exclude_unset=True)
        if "model_id" in dumped:
            if body.model_id is not None:
                model = await db.get(Model, body.model_id)
                if not model:
                    raise HTTPException(status_code=400, detail="模型不存在")
            row.model_id = body.model_id
        if "work_mode" in dumped and body.work_mode is not None:
            row.work_mode = body.work_mode
        row.updated_at = utcnow()
        await db.flush()
        out = SessionOut.model_validate(row)
    from app.agent.executor import Executor
    from app.agent.manager import manager

    agent = manager.get(session_id)
    if agent is not None:
        if "model_id" in body.model_dump(exclude_unset=True):
            agent.executor = await Executor.from_session_id(session_id)
        if out.work_mode:
            agent.set_work_mode(out.work_mode)
    return out


@router.delete("/sessions/{session_id}")
async def api_delete_session(session_id: str):
    _require_db()
    from app.agent.manager import manager

    await manager.drop(session_id)
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        row = await db.get(ChatSession, session_id)
        if not row:
            raise HTTPException(status_code=404, detail="session not found")
        row.status = "deleted"
        row.updated_at = utcnow()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def api_session_messages(session_id: str):
    _require_db()
    rows = await list_messages(session_id)
    return [
        {
            "id": r.id,
            "public_id": r.public_id,
            "session_id": r.session_id,
            "agent_id": r.agent_id,
            "role": r.role,
            "content": r.content,
            "tool_call_id": r.tool_call_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/sessions/{session_id}/tool-logs", response_model=list[ToolLogOut])
async def api_session_tool_logs(session_id: str):
    _require_db()
    rows = await list_tool_logs(session_id)
    return [ToolLogOut.model_validate(r) for r in rows]


@router.get("/sessions/{session_id}/tool-logs/{tool_call_id}", response_model=ToolLogOut)
async def api_tool_log_detail(session_id: str, tool_call_id: str):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        row = (
            await db.scalars(
                select(ToolLog)
                .where(ToolLog.session_id == session_id, ToolLog.tool_call_id == tool_call_id)
                .order_by(ToolLog.id.desc())
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="tool log not found")
        return ToolLogOut.model_validate(row)


@router.get("/sessions/{session_id}/approvals/{approval_id}")
async def api_approval_detail(session_id: str, approval_id: str):
    """审批详情：优先内存 pending，否则 404（完成后详情走 tool-logs）。"""
    pending = gate.get(approval_id)
    if pending and pending.session_id == session_id:
        return {
            "approval_id": pending.approval_id,
            "session_id": pending.session_id,
            "agent_id": pending.agent_id,
            "tool": pending.tool,
            "args": pending.args,
            "reason": pending.reason,
            "status": "pending",
        }
    raise HTTPException(status_code=404, detail="approval not found or already resolved")


@router.get("/sessions/{session_id}/subagent-runs")
async def api_subagent_runs(session_id: str):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        rows = list(
            (
                await db.scalars(
                    select(SubAgentRun)
                    .where(SubAgentRun.main_session_id == session_id)
                    .order_by(SubAgentRun.id.asc())
                )
            ).all()
        )
        return [
            {
                "id": r.id,
                "subagent_id": r.subagent_id,
                "kind": r.kind,
                "behavior_desc": r.behavior_desc,
                "goal": r.goal,
                "status": r.status,
                "result": r.result,
                "late": r.late,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]


# ---------- providers / models ----------


@router.get("/providers", response_model=list[ProviderOut])
async def api_list_providers():
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        rows = list((await db.scalars(select(Provider).order_by(Provider.id.asc()))).all())
        return [_provider_out(p) for p in rows]


@router.post("/providers", response_model=ProviderOut)
async def api_create_provider(body: ProviderCreate):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        exists = (await db.scalars(select(Provider).where(Provider.provider_id == body.provider_id))).first()
        if exists:
            raise HTTPException(status_code=409, detail="provider_id 已存在")
        p = Provider(
            provider_id=body.provider_id,
            display_name=body.display_name,
            base_url=body.base_url.rstrip("/"),
            api_key_encrypted=_encrypt_api_key("" if body.api_key_from_env else body.api_key),
            api_key_from_env=bool(body.api_key_from_env),
            api_key_env=body.api_key_env if body.api_key_from_env else None,
        )
        db.add(p)
        await db.flush()
        return _provider_out(p)


@router.get("/providers/{provider_id}", response_model=ProviderOut)
async def api_get_provider(provider_id: int):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        p = await db.get(Provider, provider_id)
        if not p:
            raise HTTPException(status_code=404, detail="provider not found")
        return _provider_out(p)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def api_patch_provider(provider_id: int, body: ProviderUpdate):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        p = await db.get(Provider, provider_id)
        if not p:
            raise HTTPException(status_code=404, detail="provider not found")
        if body.display_name is not None:
            p.display_name = body.display_name
        if body.base_url is not None:
            p.base_url = body.base_url.rstrip("/")
        if body.api_key_from_env is not None:
            p.api_key_from_env = bool(body.api_key_from_env)
            if not p.api_key_from_env:
                p.api_key_env = None
        if body.api_key_env is not None:
            p.api_key_env = body.api_key_env
        if body.api_key is not None:
            p.api_key_encrypted = _encrypt_api_key(body.api_key)
        await db.flush()
        return _provider_out(p)


@router.delete("/providers/{provider_id}")
async def api_delete_provider(provider_id: int):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        p = await db.get(Provider, provider_id)
        if not p:
            raise HTTPException(status_code=404, detail="provider not found")
        model_ids = list((await db.scalars(select(Model.id).where(Model.provider_id == provider_id))).all())
        cfg = (await db.scalars(select(AppConfig).where(AppConfig.key == "default_model_id"))).first()
        if cfg and cfg.value:
            try:
                if int(cfg.value) in set(model_ids):
                    cfg.value = None
            except ValueError:
                pass
        await db.delete(p)
    return {"ok": True}


@router.post("/providers/{provider_id}/test")
async def api_test_provider(provider_id: int, body: ProviderTestBody):
    """对指定模型 id（供应商侧 model 字符串）做 hello 探测。"""
    _require_db()
    from app.core.crypto import provider_api_key

    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        p = await db.get(Provider, provider_id)
        if not p:
            raise HTTPException(status_code=404, detail="provider not found")
        try:
            api_key = provider_api_key(p)
        except Exception as e:
            return {"ok": False, "error": str(e), "model": body.model_id}
        base = p.base_url
    return await _probe_chat(base, api_key, body.model_id)


@router.post("/llm/probe")
async def api_llm_probe(body: LlmProbeBody):
    """用表单里的 base_url / 密钥探测某个模型，不必先保存。"""
    from app.core.crypto import env_api_key, provider_api_key

    api_key = body.api_key
    env_name = body.api_key_env
    if body.provider_id is not None:
        _require_db()
        async with session_scope() as db:
            if db is None:
                raise HTTPException(status_code=503, detail="MySQL 不可用")
            p = await db.get(Provider, body.provider_id)
            if not p:
                raise HTTPException(status_code=404, detail="provider not found")
            if not env_name:
                env_name = p.api_key_env
            if api_key is None and not body.api_key_from_env:
                try:
                    api_key = provider_api_key(p)
                except Exception as e:
                    return {"ok": False, "error": str(e), "model": body.model_id}
    if body.api_key_from_env:
        api_key = env_api_key(env_name or "")
        if not api_key:
            return {
                "ok": False,
                "error": f"环境变量 {env_name or '(未填写)'} 未设置",
                "model": body.model_id,
            }
    return await _probe_chat(body.base_url, api_key or "", body.model_id)


@router.post("/models/{model_id}/test")
async def api_test_model(model_id: int):
    """对已保存的某一条模型做 hello 探测。"""
    _require_db()
    from app.core.crypto import provider_api_key

    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        m = (
            await db.scalars(select(Model).options(selectinload(Model.provider)).where(Model.id == model_id))
        ).first()
        if not m or not m.provider:
            raise HTTPException(status_code=404, detail="model not found")
        try:
            api_key = provider_api_key(m.provider)
        except Exception as e:
            return {"ok": False, "error": str(e), "model": m.model_id}
        base = m.provider.base_url
        name = m.model_id
    return await _probe_chat(base, api_key, name)


@router.get("/providers/{provider_id}/models", response_model=list[ModelOut])
async def api_list_provider_models(provider_id: int):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        p = await db.get(Provider, provider_id)
        if not p:
            raise HTTPException(status_code=404, detail="provider not found")
        rows = list(
            (
                await db.scalars(
                    select(Model).options(selectinload(Model.provider)).where(Model.provider_id == provider_id)
                )
            ).all()
        )
        return [_model_out(m) for m in rows]


@router.post("/providers/{provider_id}/models", response_model=ModelOut)
async def api_create_model(provider_id: int, body: ModelCreate):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        p = await db.get(Provider, provider_id)
        if not p:
            raise HTTPException(status_code=404, detail="provider not found")
        exists = (
            await db.scalars(select(Model).where(Model.provider_id == provider_id, Model.model_id == body.model_id))
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="该供应商下 model_id 已存在")
        m = Model(
            provider_id=provider_id,
            model_id=body.model_id,
            display_name=body.display_name,
            context_window=body.context_window,
            temperature=body.temperature,
        )
        db.add(m)
        await db.flush()
        m.provider = p
        return _model_out(m)


@router.get("/models", response_model=list[ModelOut])
async def api_list_models():
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        rows = list(
            (await db.scalars(select(Model).options(selectinload(Model.provider)).order_by(Model.id.asc()))).all()
        )
        return [_model_out(m) for m in rows]


@router.get("/models/resolved-default")
async def resolved_default():
    _require_db()
    mid = await resolve_default_model_id()
    if mid is None:
        return {"model": None, "reason": "上一次使用 > 兜底 > 空"}
    async with session_scope() as db:
        if db is None:
            return {"model": None}
        m = (
            await db.scalars(select(Model).options(selectinload(Model.provider)).where(Model.id == mid))
        ).first()
        if not m:
            return {"model": None}
        return {"model": _model_out(m)}


@router.patch("/models/{model_id}", response_model=ModelOut)
async def api_patch_model(model_id: int, body: ModelUpdate):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        m = (await db.scalars(select(Model).options(selectinload(Model.provider)).where(Model.id == model_id))).first()
        if not m:
            raise HTTPException(status_code=404, detail="model not found")
        if body.model_id is not None and body.model_id != m.model_id:
            exists = (
                await db.scalars(
                    select(Model).where(
                        Model.provider_id == m.provider_id,
                        Model.model_id == body.model_id,
                        Model.id != model_id,
                    )
                )
            ).first()
            if exists:
                raise HTTPException(status_code=409, detail="该供应商下 model_id 已存在")
            m.model_id = body.model_id
        if body.display_name is not None:
            m.display_name = body.display_name
        if body.context_window is not None:
            m.context_window = body.context_window
        if body.temperature is not None:
            m.temperature = body.temperature
        await db.flush()
        return _model_out(m)


@router.delete("/models/{model_id}")
async def api_delete_model(model_id: int):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        m = await db.get(Model, model_id)
        if not m:
            raise HTTPException(status_code=404, detail="model not found")
        cfg = (await db.scalars(select(AppConfig).where(AppConfig.key == "default_model_id"))).first()
        if cfg and cfg.value:
            try:
                if int(cfg.value) == model_id:
                    cfg.value = None
            except ValueError:
                pass
        await db.delete(m)
    return {"ok": True}


@router.get("/config/default-model")
async def get_default_model():
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        cfg = (await db.scalars(select(AppConfig).where(AppConfig.key == "default_model_id"))).first()
        mid = None
        if cfg and cfg.value:
            try:
                mid = int(cfg.value)
            except ValueError:
                mid = None
        return {"default_model_id": mid}


@router.put("/config/default-model")
async def put_default_model(body: DefaultModelBody):
    _require_db()
    async with session_scope() as db:
        if db is None:
            raise HTTPException(status_code=503, detail="MySQL 不可用")
        if body.default_model_id is not None:
            m = await db.get(Model, body.default_model_id)
            if not m:
                raise HTTPException(status_code=400, detail="模型不存在")
        cfg = (await db.scalars(select(AppConfig).where(AppConfig.key == "default_model_id"))).first()
        value = str(body.default_model_id) if body.default_model_id is not None else None
        if cfg is None:
            cfg = AppConfig(key="default_model_id", value=value)
            db.add(cfg)
        else:
            cfg.value = value
            cfg.updated_at = utcnow()
        return {"default_model_id": body.default_model_id}


@router.get("/status")
async def api_status():
    n_models = 0
    try:
        n_models = await count_models()
    except Exception as e:
        logger.debug("count_models failed: %s", e)
    redis_ok = False
    try:
        from app.core.rtstore import redis_available

        redis_ok = await redis_available()
    except Exception as e:
        logger.debug("redis status failed: %s", e)
    return {"mysql": is_available(), "redis": redis_ok, "models": n_models, "encryption": encryption_ready()}
