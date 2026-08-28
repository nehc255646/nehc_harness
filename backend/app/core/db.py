"""异步 MySQL 引擎 — 连不上仅告警，降级内存运行"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger("harness.db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_available = False
_loop: asyncio.AbstractEventLoop | None = None


def is_available() -> bool:
    if not _available or _engine is None:
        return False
    try:
        return _loop is asyncio.get_running_loop()
    except RuntimeError:
        return False


async def init_db() -> bool:
    """启动时探测 MySQL。失败不阻塞服务。"""
    global _engine, _sessionmaker, _available, _loop
    loop = asyncio.get_running_loop()
    # pytest 每测例新 loop：旧 engine 不能跨 loop 复用
    if _engine is not None and _loop is not loop:
        _engine = None
        _sessionmaker = None
        _available = False
        _loop = None
    if _available and _engine is not None:
        return True
    try:
        _engine = create_async_engine(
            settings.mysql_dsn,
            pool_pre_ping=True,
            poolclass=NullPool,
        )
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
        _available = True
        _loop = loop
        logger.info(
            "MySQL connected: %s@%s:%s/%s",
            settings.mysql_user,
            settings.mysql_host,
            settings.mysql_port,
            settings.mysql_database,
        )
        return True
    except Exception as e:
        logger.warning("MySQL unavailable (%s): %s — 降级为仅内存运行", settings.mysql_dsn.split("@")[-1], e)
        _available = False
        _engine = None
        _sessionmaker = None
        return False


async def close_db() -> None:
    global _engine, _sessionmaker, _available, _loop
    if _engine is not None:
        try:
            await _engine.dispose()
        except Exception as e:
            logger.debug("MySQL dispose failed: %s", e)
    _engine = None
    _sessionmaker = None
    _available = False
    _loop = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession | None]:
    if not _available or _sessionmaker is None:
        yield None
        return
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
