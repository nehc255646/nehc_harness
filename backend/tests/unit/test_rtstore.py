"""M4 Redis 实时层 — 状态 TTL / pending / 放行规则 / 摘要缓存"""

import asyncio
import fnmatch

import pytest

from app.core import rtstore
from app.permissions.gate import ApprovalGate


class FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}

    async def ping(self):
        return True

    async def scan_iter(self, match=None, count=None):
        for k in list(self.kv):
            if match is None or fnmatch.fnmatch(k, match):
                yield k

    async def set(self, key, value, ex=None):
        self.kv[key] = value
        if ex is not None:
            self.expires[key] = int(ex)

    async def get(self, key):
        return self.kv.get(key)

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.kv:
                self.kv.pop(k, None)
                n += 1
            if k in self.hashes:
                self.hashes.pop(k, None)
                n += 1
            self.expires.pop(k, None)
        return n

    async def expire(self, key, ttl):
        self.expires[key] = int(ttl)
        return True

    async def hset(self, key, mapping=None):
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            bucket.update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping or {})

    async def hdel(self, key, *fields):
        bucket = self.hashes.get(key, {})
        n = 0
        for f in fields:
            if f in bucket:
                del bucket[f]
                n += 1
        return n

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(rtstore, "get_redis", _get)
    return fake


async def test_agent_state_roundtrip(fake_redis):
    await rtstore.set_agent_state("s1", "main", "awaiting_approval")
    assert await rtstore.get_agent_state("s1", "main") == "awaiting_approval"
    assert fake_redis.expires[rtstore.key_agent_state("s1", "main")] >= 300


async def test_session_rules_roundtrip(fake_redis):
    rules = [{"kind": "shell_prefix", "pattern": "echo hello"}]
    await rtstore.set_session_rules("s2", rules)
    assert await rtstore.get_session_rules("s2") == rules


async def test_pending_replace_prunes_stale(fake_redis):
    await rtstore.put_pending("s3", "old", {"approval_id": "old", "tool": "shell"})
    live = [{"approval_id": "new", "tool": "write", "args": {}, "reason": "x"}]
    await rtstore.replace_pending("s3", live)
    listed = await rtstore.list_pending("s3")
    assert [p["approval_id"] for p in listed] == ["new"]


async def test_summary_cache_roundtrip(fake_redis):
    payload = {"text": "摘要", "version": 2, "covered_count": 8, "pending_slid": []}
    await rtstore.set_summary_cache("s4", payload)
    got = await rtstore.get_summary_cache("s4")
    assert got["version"] == 2
    assert got["text"] == "摘要"


async def test_purge_session_clears_keys(fake_redis):
    await rtstore.set_agent_state("s5", "main", "idle")
    await rtstore.set_session_rules("s5", [{"kind": "tool", "pattern": "write"}])
    await rtstore.put_pending("s5", "a1", {"approval_id": "a1"})
    await rtstore.set_summary_cache("s5", {"text": "t", "version": 1})
    await rtstore.purge_session("s5")
    assert await rtstore.get_agent_state("s5", "main") is None
    assert await rtstore.get_session_rules("s5") == []
    assert await rtstore.list_pending("s5") == []
    assert await rtstore.get_summary_cache("s5") is None


async def test_hello_restores_rules_from_redis(fake_redis):
    """内存空时从 Redis 灌回会话放行规则（后端重启后仍生效）。"""
    g = ApprovalGate()
    sid = "s_restore"
    await rtstore.set_session_rules(sid, [{"kind": "tool", "pattern": "write"}])
    assert g.get_session_rules(sid) == []
    cached = await rtstore.get_session_rules(sid)
    for rule in cached:
        g.add_session_rule(sid, rule, persist=False)
    assert g.get_session_rules(sid) == [{"kind": "tool", "pattern": "write"}]


def test_sanitize_session_rule_rejects_garbage():
    from app.permissions.gate import sanitize_session_rule

    assert sanitize_session_rule({"kind": "tool", "pattern": "shell"}) == {"kind": "tool", "pattern": "shell"}
    assert sanitize_session_rule({"kind": "all", "pattern": "shell"}) is None
    assert sanitize_session_rule({"kind": "tool", "pattern": ""}) is None
    assert sanitize_session_rule("not-a-dict") is None


async def test_gate_request_mirrors_pending(fake_redis):
    g = ApprovalGate()
    aid, fut = await g.request_approval("s6", "main", "shell", {"command": "echo hi"}, "reason")
    await asyncio.sleep(0.05)
    listed = await rtstore.list_pending("s6")
    assert any(p.get("approval_id") == aid for p in listed)
    assert g.resolve(aid, "approve") is True
    await asyncio.sleep(0.05)
    assert await rtstore.list_pending("s6") == []
    assert fut.result()[0] is True
