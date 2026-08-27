"""LangChain ChatOpenAI 封装 + 重试退避"""

import asyncio
import logging
import os
from typing import Any

from app.core.config import settings

logger = logging.getLogger("harness.executor")

# 延迟导入，避免无 key 时启动失败
try:
    from langchain_openai import ChatOpenAI
except Exception:
    ChatOpenAI = None  # type: ignore


class Executor:
    """按 env 或显式参数实例化 ChatOpenAI，支持 bind_tools + 流式"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        retry_count: int | None = None,
    ):
        self.model = model or settings.openai_model or "gpt-4o-mini"
        self.base_url = base_url or settings.openai_base_url or None
        self.api_key = api_key or settings.openai_api_key or "sk-test"
        self.temperature = temperature
        self.retry_count = retry_count if retry_count is not None else settings.retry_count
        self._llm = None
        self._init_llm()

    def _init_llm(self):
        if ChatOpenAI is None:
            logger.warning("ChatOpenAI not available")
            return
        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": self.api_key,
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
        # messages 为 OpenAI 格式，需转为 LangChain 消息
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
                # 可能含 tool_calls
                kwargs: dict[str, Any] = {"content": content or ""}
                if m.get("tool_calls"):
                    kwargs["tool_calls"] = m["tool_calls"]
                lc_messages.append(AIMessage(**kwargs))
            elif role == "tool":
                lc_messages.append(ToolMessage(content=content, tool_call_id=m.get("tool_call_id", "")))
        return await llm.ainvoke(lc_messages)

    async def astream(self, messages: list[dict], tools: list | None = None):
        """流式生成，yield chunk"""
        if self._llm is None:
            raise RuntimeError("LLM 未初始化")
        llm = self.bind_tools(tools) if tools else self._llm
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
                    kwargs["tool_calls"] = m["tool_calls"]
                lc_messages.append(AIMessage(**kwargs))
            elif role == "tool":
                lc_messages.append(ToolMessage(content=content, tool_call_id=m.get("tool_call_id", "")))
        async for chunk in llm.astream(lc_messages):
            yield chunk

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
