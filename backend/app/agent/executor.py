"""LangChain ChatOpenAI 封装 + 重试退避 (M1 占位)"""

import asyncio
import logging

logger = logging.getLogger("harness.executor")


class Executor:
    """按 Session.model_id → Model → Provider 解析实例化 ChatOpenAI (M3 接入)"""

    def __init__(self, retry_count: int = 1):
        self.retry_count = retry_count

    async def ainvoke(self, messages, tools=None):
        # M1 占位：未接真实模型时抛提示
        raise NotImplementedError("Executor 尚未接入真实模型 — M1 下一步实现 ChatOpenAI.bind_tools")

    async def invoke_with_retry(self, messages, tools=None):
        last_err = None
        for attempt in range(self.retry_count + 1):
            try:
                return await self.ainvoke(messages, tools)
            except Exception as e:
                last_err = e
                if attempt < self.retry_count:
                    await asyncio.sleep(2**attempt)
                else:
                    raise last_err
