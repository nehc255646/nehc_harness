"""手写 asyncio loop — 对应 PLAN.md §2.1 (M1 占位)"""

import asyncio
import logging

logger = logging.getLogger("harness.loop")


class AgentLoop:
    """每轮：drain 队列 → 构造 messages → 调模型 → 分发工具(含用户门) → 原子回填"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.state = "idle"

    async def run(self):
        logger.info("AgentLoop started for session %s", self.session_id)
        self.state = "running"
        # TODO: 实现 PLAN.md §2.1 完整 loop
        while True:
            event = await self.queue.get()
            logger.debug("Loop drain: %s", event)
            # 占位 idle
            self.state = "idle"
            await asyncio.sleep(0.1)

    async def enqueue(self, event: dict):
        await self.queue.put(event)
