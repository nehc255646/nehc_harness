"""FastAPI 入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest import router as rest_router
from app.api.ws import router as ws_router
from app.core.crypto import encryption_ready
from app.core.db import close_db, init_db
from app.core.logging import setup_logging
from app.core.redis import close_redis, get_redis
from app.persist import maybe_import_env_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log = logging.getLogger("harness")
    if not encryption_ready():
        log.warning("ENCRYPTION_KEY 未配置或非法 — Provider api_key 加密将不可用")
    await init_db()
    try:
        await maybe_import_env_provider()
    except Exception:
        log.exception("env 导入 Provider 失败")
    await get_redis()
    yield
    await close_redis()
    await close_db()


app = FastAPI(title="Agent Harness", version="0.1.0", lifespan=lifespan)

# CORS — 允许 Vite dev server（无 cookie 鉴权，不开 credentials）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(ws_router)


@app.get("/")
async def root():
    return {"name": "Agent Harness", "version": "0.1.0", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}
