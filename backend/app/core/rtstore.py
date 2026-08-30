"""Redis 实时层

权威在内存 AgentManager / ApprovalGate 与 MySQL；Redis 镜像：
- agent 状态 TTL（过期不代表结束）
- pending 审批元数据
- 会话放行规则
- 摘要缓存（版本号 + 未合并滑出缓冲）

连不上则全部 no-op，不阻塞 loop。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger("neharness.rtstore")

PREFIX = "neharness"


def _state_ttl() -> int:
    return max(300, int(settings.approval_timeout) * 2)


def _pending_ttl() -> int:
    return int(settings.approval_timeout) + 60


def _session_ttl() -> int:
    return 86400


def key_agent_state(session_id: str, agent_id: str) -> str:
    return f"{PREFIX}:agent:{session_id}:{agent_id}:state"


def key_allow_rules(session_id: str) -> str:
    return f"{PREFIX}:session:{session_id}:allow_rules"


def key_pending(session_id: str) -> str:
    return f"{PREFIX}:session:{session_id}:pending"


def key_summary(session_id: str) -> str:
    return f"{PREFIX}:session:{session_id}:summary_cache"


def fire_and_forget(coro) -> None:
    """同步路径投递 Redis 写；无 running loop 或 Redis 失败时丢弃。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if asyncio.iscoroutine(coro):
            coro.close()
        return

    async def _run():
        try:
            await coro
        except Exception:
            logger.debug("rtstore background write failed", exc_info=True)

    loop.create_task(_run())


async def redis_available() -> bool:
    try:
        r = await get_redis()
        if r is None:
            return False
        await r.ping()
        return True
    except Exception:
        return False


async def set_agent_state(session_id: str, agent_id: str, state: str) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.set(key_agent_state(session_id, agent_id), state, ex=_state_ttl())
    except Exception:
        logger.debug("set_agent_state failed", exc_info=True)


async def get_agent_state(session_id: str, agent_id: str) -> str | None:
    r = await get_redis()
    if r is None:
        return None
    try:
        val = await r.get(key_agent_state(session_id, agent_id))
        return str(val) if val is not None else None
    except Exception:
        logger.debug("get_agent_state failed", exc_info=True)
        return None


async def set_session_rules(session_id: str, rules: list[dict]) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.set(key_allow_rules(session_id), json.dumps(rules, ensure_ascii=False), ex=_session_ttl())
    except Exception:
        logger.debug("set_session_rules failed", exc_info=True)


async def get_session_rules(session_id: str) -> list[dict]:
    r = await get_redis()
    if r is None:
        return []
    try:
        raw = await r.get(key_allow_rules(session_id))
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        logger.debug("get_session_rules failed", exc_info=True)
        return []


async def delete_session_rules(session_id: str) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.delete(key_allow_rules(session_id))
    except Exception:
        logger.debug("delete_session_rules failed", exc_info=True)


async def put_pending(session_id: str, approval_id: str, payload: dict[str, Any]) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        key = key_pending(session_id)
        await r.hset(key, mapping={approval_id: json.dumps(payload, ensure_ascii=False)})
        await r.expire(key, _pending_ttl())
    except Exception:
        logger.debug("put_pending failed", exc_info=True)


async def delete_pending(session_id: str, approval_id: str) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.hdel(key_pending(session_id), approval_id)
    except Exception:
        logger.debug("delete_pending failed", exc_info=True)


async def list_pending(session_id: str) -> list[dict[str, Any]]:
    r = await get_redis()
    if r is None:
        return []
    try:
        raw = await r.hgetall(key_pending(session_id))
        out: list[dict[str, Any]] = []
        if not raw:
            return out
        for blob in raw.values():
            try:
                item = json.loads(blob)
                if isinstance(item, dict):
                    out.append(item)
            except Exception:
                logger.debug("skip malformed pending payload", exc_info=True)
        return out
    except Exception:
        logger.debug("list_pending failed", exc_info=True)
        return []


async def replace_pending(session_id: str, payloads: list[dict[str, Any]]) -> None:
    """内存权威：用当前 live pending 覆盖 Redis，清掉崩溃残留。"""
    r = await get_redis()
    if r is None:
        return
    key = key_pending(session_id)
    try:
        await r.delete(key)
        if not payloads:
            return
        mapping = {str(p["approval_id"]): json.dumps(p, ensure_ascii=False) for p in payloads if p.get("approval_id")}
        if mapping:
            await r.hset(key, mapping=mapping)
            await r.expire(key, _pending_ttl())
    except Exception:
        logger.debug("replace_pending failed", exc_info=True)


async def set_summary_cache(session_id: str, payload: dict[str, Any]) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.set(key_summary(session_id), json.dumps(payload, ensure_ascii=False), ex=_session_ttl())
    except Exception:
        logger.debug("set_summary_cache failed", exc_info=True)


async def get_summary_cache(session_id: str) -> dict[str, Any] | None:
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key_summary(session_id))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("get_summary_cache failed", exc_info=True)
        return None


async def purge_session(session_id: str) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        keys = [
            key_allow_rules(session_id),
            key_pending(session_id),
            key_summary(session_id),
            key_agent_state(session_id, "main"),
        ]
        # 子 agent state 键按前缀尽力扫描清理（不支持的客户端跳过，键靠 TTL 过期）
        try:
            pattern = f"{PREFIX}:agent:{session_id}:*:state"
            async for k in r.scan_iter(match=pattern, count=100):
                k = str(k)
                if k not in keys:
                    keys.append(k)
        except Exception:
            logger.debug("scan agent state keys failed", exc_info=True)
        await r.delete(*keys)
    except Exception:
        logger.debug("purge_session failed", exc_info=True)
