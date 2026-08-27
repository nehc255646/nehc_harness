"""REST API — 会话/历史/Provider·Model 配置占位"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["rest"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/sessions")
async def list_sessions():
    return {"sessions": [], "note": "M3 实现 — 当前占位"}


@router.get("/providers")
async def list_providers():
    return {"providers": [], "note": "M3 实现 — 当前占位"}


@router.get("/models/resolved-default")
async def resolved_default():
    return {"model": None, "note": "M3 实现 — 上一次使用 > 兜底 > 空"}


@router.get("/config/default-model")
async def get_default_model():
    return {"default_model_id": None}
