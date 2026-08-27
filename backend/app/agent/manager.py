"""AgentManager 进程内注册表 — 单进程硬约束"""

import logging

from app.agent.loop import AgentLoop

logger = logging.getLogger("harness.manager")


class AgentManager:
    def __init__(self):
        self._agents: dict[str, AgentLoop] = {}

    def get_or_create(self, session_id: str) -> AgentLoop:
        if session_id not in self._agents:
            self._agents[session_id] = AgentLoop(session_id)
            logger.info("Agent created: %s", session_id)
        return self._agents[session_id]

    def get(self, session_id: str) -> AgentLoop | None:
        return self._agents.get(session_id)

    def all_ids(self) -> list[str]:
        return list(self._agents.keys())


manager = AgentManager()
