"""LangChain ChatOpenAI 封装 + OpenAI 兼容流式（含思考通道）"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.agent.stream import thinking_extra_body
from app.core.config import settings

logger = logging.getLogger("harness.executor")

# 延迟导入，避免无 key 时启动失败
try:
    from langchain_openai import ChatOpenAI
except Exception:
    ChatOpenAI = None  # type: ignore


def _lc_tool_calls(raw: list) -> list[dict]:
    """历史里的 tool_calls 补齐 LangChain / OpenAI 需要的 type=tool_call。"""
    out: list[dict] = []
    for tc in raw or []:
        if not isinstance(tc, dict):
            continue
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        item = {
            "name": tc.get("name") or "",
            "args": args,
            "id": tc.get("id") or "",
            "type": "tool_call",
        }
        out.append(item)
    return out


def _to_lc_messages(messages: list[dict]) -> list:
    """OpenAI 格式 → LangChain 消息（ainvoke/astream 共用）。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    lc_messages = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            kwargs: dict[str, Any] = {"content": content or ""}
            if m.get("tool_calls"):
                kwargs["tool_calls"] = _lc_tool_calls(m["tool_calls"])
            lc_messages.append(AIMessage(**kwargs))
        elif role == "tool":
            tm_kwargs: dict[str, Any] = {"content": str(content or ""), "tool_call_id": m.get("tool_call_id") or ""}
            if m.get("name"):
                tm_kwargs["name"] = m["name"]
            lc_messages.append(ToolMessage(**tm_kwargs))
    return lc_messages


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """历史 dict → OpenAI chat messages。不回传 thinking。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role") or "user"
        item: dict[str, Any] = {"role": role, "content": m.get("content") or ""}
        if role == "assistant" and m.get("tool_calls"):
            oai_tcs = []
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                args = tc.get("args")
                if isinstance(args, dict):
                    arg_s = json.dumps(args, ensure_ascii=False)
                else:
                    arg_s = str(args or "")
                oai_tcs.append(
                    {
                        "id": tc.get("id") or "",
                        "type": "function",
                        "function": {"name": tc.get("name") or "", "arguments": arg_s},
                    }
                )
            if oai_tcs:
                item["tool_calls"] = oai_tcs
        if role == "tool":
            item["tool_call_id"] = m.get("tool_call_id") or ""
            if m.get("name"):
                item["name"] = m["name"]
        out.append(item)
    return out


def _openai_tools(tools: list | None) -> list | None:
    if not tools:
        return None
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool
    except Exception:
        return None
    out = []
    for t in tools:
        try:
            out.append(convert_to_openai_tool(t))
        except Exception:
            logger.debug("convert tool failed: %s", t, exc_info=True)
    return out or None


def _is_bad_request(err: BaseException) -> bool:
    code = getattr(err, "status_code", None)
    if code == 400:
        return True
    return "BadRequest" in type(err).__name__


class Executor:
    """按 env 或显式参数实例化 ChatOpenAI，支持 bind_tools + 流式"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        retry_count: int | None = None,
        unresolved: bool = False,
        request_thinking: bool = False,
        reasoning_effort: str | None = None,
    ):
        self.model = model or settings.openai_model or "gpt-4o-mini"
        self.base_url = base_url or settings.openai_base_url or None
        self.temperature = temperature
        self.retry_count = retry_count if retry_count is not None else settings.retry_count
        self.context_window = 128000
        self.model_pk: int | None = None
        self.unresolved = unresolved
        self.demo = False
        self.request_thinking = request_thinking
        self.reasoning_effort = reasoning_effort
        self._llm = None
        if unresolved:
            self.api_key = ""
            self.demo = True
            return
        if api_key is not None:
            self.api_key = api_key
            self.demo = False
        else:
            self.api_key = settings.openai_api_key or "sk-test"
            self.demo = not bool(settings.openai_api_key)
        self._init_llm()

    @classmethod
    async def from_session_id(cls, session_id: str) -> Executor:
        """Session.model_id → Model → Provider.base_url + api_key + model.model_id"""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.core.crypto import provider_api_key
        from app.core.db import is_available, session_scope
        from app.models import ChatSession, Model

        if is_available():
            try:
                async with session_scope() as db:
                    if db is not None:
                        sess = await db.get(ChatSession, session_id)
                        if sess and sess.model_id:
                            try:
                                model = (
                                    await db.scalars(
                                        select(Model)
                                        .options(selectinload(Model.provider))
                                        .where(Model.id == sess.model_id)
                                    )
                                ).first()
                                if not model or not model.provider:
                                    logger.warning("session model missing: session=%s model_id=%s", session_id, sess.model_id)
                                    return cls(unresolved=True)
                                key = provider_api_key(model.provider)
                                inst = cls(
                                    model=model.model_id,
                                    base_url=model.provider.base_url,
                                    api_key=key,
                                    temperature=model.temperature,
                                    request_thinking=bool(getattr(model, "request_thinking", False)),
                                    reasoning_effort=getattr(model, "reasoning_effort", None),
                                )
                                inst.context_window = model.context_window
                                inst.model_pk = model.id
                                return inst
                            except Exception as e:
                                logger.warning("Executor.from_session_id model bind failed: %s", e)
                                return cls(unresolved=True)
            except Exception as e:
                logger.warning("Executor.from_session_id failed, fallback env: %s", e)
        return cls()

    def _init_llm(self):
        if ChatOpenAI is None:
            logger.warning("ChatOpenAI not available")
            return
        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": self.api_key or "no-key",
            "temperature": self.temperature,
            "streaming": True,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        try:
            self._llm = ChatOpenAI(**kwargs)
            logger.info("ChatOpenAI initialized: model=%s base_url=%s", self.model, self.base_url or "default")
        except Exception as e:
            logger.warning("ChatOpenAI init failed: %s", e)
            self._llm = None

    def bind_tools(self, tools: list):
        if self._llm is None:
            raise RuntimeError("LLM 未初始化，检查 OPENAI_API_KEY / OPENAI_BASE_URL")
        return self._llm.bind_tools(tools)

    async def ainvoke(self, messages: list[dict], tools: list | None = None):
        """非流式调用 (用于测试/简单场景)"""
        if self._llm is None:
            raise RuntimeError("LLM 未初始化")
        llm = self.bind_tools(tools) if tools else self._llm
        return await llm.ainvoke(_to_lc_messages(messages))

    def _openai_client(self):
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {
            "api_key": self.api_key or "no-key",
            "timeout": 45.0,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncOpenAI(**kwargs)

    async def astream_openai(self, messages: list[dict], tools: list | None = None, extra_body: dict | None = None):
        """原生 /chat/completions 流，保留 reasoning_content。"""
        client = self._openai_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "stream": True,
            "temperature": self.temperature,
        }
        oai_tools = _openai_tools(tools)
        if oai_tools:
            kwargs["tools"] = oai_tools
        if extra_body:
            kwargs["extra_body"] = extra_body
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            yield chunk

    async def astream(self, messages: list[dict], tools: list | None = None, extra_body: dict | None = None):
        """优先 OpenAI SDK 流式；失败再走 LangChain（可能丢思考字段）。"""
        try:
            async for chunk in self.astream_openai(messages, tools, extra_body=extra_body):
                yield chunk
            return
        except ImportError:
            logger.warning("openai SDK 不可用，回退 LangChain astream")
        except Exception as e:
            if extra_body and _is_bad_request(e):
                raise
            logger.warning("OpenAI 流式失败，回退 LangChain: %s", e)
        if self._llm is None:
            raise RuntimeError("LLM 未初始化")
        llm = self.bind_tools(tools) if tools else self._llm
        async for chunk in llm.astream(_to_lc_messages(messages)):
            yield chunk

    async def astream_with_retry(self, messages: list[dict], tools: list | None = None):
        """流式生成 + 指数退避重试 — loop 主调用入口。

        仅在尚未产出任何 chunk 时重试：中途失败重试会导致已广播的部分内容
        与重试全文拼接重复，故已开始输出后直接抛错交给上层处理。
        extra_body 若 400，去掉思考 extra 再试一次（仅首次）。
        """
        extra = thinking_extra_body(self.request_thinking, self.reasoning_effort)
        last_err: Exception | None = None
        dropped_extra = False
        for attempt in range(self.retry_count + 1):
            yielded = False
            try:
                async for chunk in self.astream(messages, tools, extra_body=extra):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                last_err = e
                if yielded:
                    raise
                if extra and not dropped_extra and _is_bad_request(e):
                    logger.warning("思考 extra_body 被拒绝，去掉后重试: %s", e)
                    extra = None
                    dropped_extra = True
                    continue
                if attempt < self.retry_count:
                    delay = 2**attempt
                    logger.warning("LLM 流式失败，%ss 后重试 (%d/%d): %s", delay, attempt + 1, self.retry_count, e)
                    await asyncio.sleep(delay)
                else:
                    raise last_err
        if last_err:
            raise last_err

    async def invoke_with_retry(self, messages: list[dict], tools: list | None = None):
        last_err = None
        for attempt in range(self.retry_count + 1):
            try:
                return await self.ainvoke(messages, tools)
            except Exception as e:
                last_err = e
                if attempt < self.retry_count:
                    delay = 2**attempt
                    logger.warning("LLM 调用失败，%ss 后重试 (%d/%d): %s", delay, attempt + 1, self.retry_count, e)
                    await asyncio.sleep(delay)
                else:
                    raise last_err
