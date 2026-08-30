"""Redis 客户端 — 连不上仅告警，不阻塞启动"""

import logging
from urllib.parse import urlparse, urlunparse

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger("neharness.redis")

_client: redis.Redis | None = None


def _safe_redis_url(url: str) -> str:
    u = urlparse(url)
    if not u.password:
        return url
    host = u.hostname or ""
    auth = f"{u.username}:***@" if u.username else "***@"
    port = f":{u.port}" if u.port else ""
    return urlunparse((u.scheme, f"{auth}{host}{port}", u.path, "", "", ""))


async def get_redis() -> redis.Redis | None:
    global _client
    if _client is not None:
        return _client
    try:
        _client = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        await _client.ping()
        logger.info("Redis connected: %s", _safe_redis_url(settings.redis_url))
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
