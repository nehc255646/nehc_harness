"""FastAPI 入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest import router as rest_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if not settings.encryption_key:
        # M3 起 api_key 加密依赖该密钥，缺失时明确告警
        logging.getLogger("harness").warning("ENCRYPTION_KEY 未配置 — Provider api_key 加密将不可用")
    # Redis 尝试连接，连不上仅告警
    await get_redis()
    yield
    await close_redis()


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
