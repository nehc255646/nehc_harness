"""FastAPI 入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest import router as rest_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.core.crypto import encryption_ready
from app.core.db import close_db, init_db
from app.core.logging import setup_logging
from app.core.redis import close_redis, get_redis
from app.persist import maybe_import_env_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log = logging.getLogger("neharness")
    if not encryption_ready():
        log.warning("ENCRYPTION_KEY 未配置或非法 — Provider api_key 加密将不可用")
    await init_db()
    try:
        await maybe_import_env_provider()
    except Exception:
        log.exception("env 导入 Provider 失败")
    if not encryption_ready():
        try:
            from app.persist import count_providers

            n = await count_providers()
        except Exception:
            n = 0
        if n:
            raise RuntimeError("已有 Provider 但 ENCRYPTION_KEY 未配置或非法，拒绝启动")
    await get_redis()
    yield
    await close_redis()
    await close_db()


app = FastAPI(title="Neharness", version="0.1.0", lifespan=lifespan)

_cors = [o.strip() for o in (settings.cors_origins or "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors or ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(ws_router)


@app.get("/")
async def root():
    return {"name": "Neharness", "version": "0.1.0", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}
