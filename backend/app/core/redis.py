"""Redis 客户端 — 连不上仅告警，不阻塞启动"""

import logging

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger("harness.redis")

_client: redis.Redis | None = None


async def get_redis() -> redis.Redis | None:
    global _client
    if _client is not None:
        return _client
    try:
        _client = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        await _client.ping()
        logger.info("Redis connected: %s", settings.redis_url)
        return _client
    except Exception as e:
        logger.warning("Redis unavailable (%s): %s — running without Redis", settings.redis_url, e)
        _client = None
        return None


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as e:
            logger.debug("Redis close failed: %s", e)
        _client = None
