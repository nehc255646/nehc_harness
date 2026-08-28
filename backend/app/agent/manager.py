"""AgentManager 进程内注册表 — 单进程硬约束"""

import asyncio
import logging

from app.agent.loop import AgentLoop

logger = logging.getLogger("harness.manager")


class AgentManager:
    def __init__(self):
        self._agents: dict[str, AgentLoop] = {}
        self._broadcast_fn = None

    def set_broadcaster(self, fn):
        self._broadcast_fn = fn
        for agent in self._agents.values():
            agent.set_broadcaster(fn)

    async def get_or_create(self, session_id: str) -> AgentLoop:
        if session_id not in self._agents:
            agent = AgentLoop(session_id, broadcaster=self._broadcast_fn)
            self._agents[session_id] = agent
            logger.info("Agent created: %s", session_id)
            try:
                await agent.hydrate_from_db()
            except Exception:
                logger.exception("hydrate_from_db failed: %s", session_id)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(agent.start())
            except RuntimeError:
                pass
        return self._agents[session_id]

    def get(self, session_id: str) -> AgentLoop | None:
        return self._agents.get(session_id)

    async def drop(self, session_id: str):
        agent = self._agents.pop(session_id, None)
        if agent:
            try:
                await agent.stop()
            except Exception:
                logger.exception("stop on drop failed: %s", session_id)

    def all_ids(self) -> list[str]:
        return list(self._agents.keys())

    async def stop(self, session_id: str):
        agent = self._agents.get(session_id)
        if agent:
            await agent.stop()

    async def stop_all(self):
        for agent in list(self._agents.values()):
            await agent.stop()


manager = AgentManager()
