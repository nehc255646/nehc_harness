"""per-agent 审批挂起/恢复 (Future + 超时)"""

import asyncio
import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger("harness.gate")


@dataclass
class PendingApproval:
    approval_id: str
    session_id: str
    agent_id: str
    tool: str
    args: dict
    reason: str
    future: asyncio.Future  # 由 request_approval 在运行中的事件循环内创建
    decision: str | None = None  # approved_once | approved_similar | rejected | timeout | blocked


class ApprovalGate:
    """单进程内存审批表 — per-agent Future，支持超时与断连兜底"""

    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}
        # 会话放行规则: session_id -> list[dict{kind, pattern}]
        self._session_rules: dict[str, list[dict]] = {}

    # ---------- 会话规则 ----------

    def get_session_rules(self, session_id: str) -> list[dict]:
        return list(self._session_rules.get(session_id, []))

    def add_session_rule(self, session_id: str, rule: dict, persist: bool = True) -> None:
        lst = self._session_rules.setdefault(session_id, [])
        if rule not in lst:
            lst.append(rule)
            logger.info("Session rule added: %s %s", session_id, rule)
        if persist:
            self._persist_rules(session_id)

    def clear_session_rules(self, session_id: str) -> None:
        self._session_rules.pop(session_id, None)
        try:
            from app.core.rtstore import delete_session_rules, fire_and_forget

            fire_and_forget(delete_session_rules(session_id))
        except Exception:
            logger.debug("clear session rules redis failed", exc_info=True)

    def _persist_rules(self, session_id: str) -> None:
        try:
            from app.core.rtstore import fire_and_forget, set_session_rules

            fire_and_forget(set_session_rules(session_id, self.get_session_rules(session_id)))
        except Exception:
            logger.debug("persist session rules failed", exc_info=True)

    # ---------- 审批 ----------

    async def request_approval(
        self, session_id: str, agent_id: str, tool: str, args: dict, reason: str
    ) -> tuple[str, asyncio.Future]:
        approval_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        pending = PendingApproval(
            approval_id=approval_id,
            session_id=session_id,
            agent_id=agent_id,
            tool=tool,
            args=args,
            reason=reason,
            future=fut,
        )
        self._pending[approval_id] = pending
        logger.info("Approval requested: %s tool=%s session=%s", approval_id, tool, session_id)
        try:
            from app.core.rtstore import fire_and_forget, put_pending

            fire_and_forget(
                put_pending(
                    session_id,
                    approval_id,
                    {
                        "approval_id": approval_id,
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "tool": tool,
                        "args": args,
                        "reason": reason,
                    },
                )
            )
        except Exception:
            logger.debug("persist pending failed", exc_info=True)
        return approval_id, fut

    def resolve(self, approval_id: str, decision: str, reason: str = "user") -> bool:
        """
        decision: approve | approve_similar | reject | timeout | disconnect
        返回是否成功消费 (单次消费，防多标签页重复)
        """
        pending = self._pending.get(approval_id)
        if not pending:
            logger.warning("Approval not found or already resolved: %s", approval_id)
            return False
        if pending.future.done():
            # 已被 wait_for 超时取消：仅清理残留，不再写结果
            if pending.future.cancelled():
                self._pending.pop(approval_id, None)
                try:
                    from app.core.rtstore import delete_pending, fire_and_forget

                    fire_and_forget(delete_pending(pending.session_id, approval_id))
                except Exception:
                    logger.debug("delete cancelled pending redis failed", exc_info=True)
                logger.info("Approval cleaned after cancel: %s", approval_id)
                return True
            return False

        # 会话放行规则写入 (approve_similar)
        if decision == "approve_similar":
            if pending.tool == "shell":
                # 取前 2 token 前缀；空命令不写入，避免放行一切
                args = pending.args if isinstance(pending.args, dict) else {}
                cmd = str(args.get("command") or args.get("cmd") or "").strip()
                tokens = cmd.split()
                prefix = " ".join(tokens[:2])
                if prefix:
                    self.add_session_rule(pending.session_id, {"kind": "shell_prefix", "pattern": prefix})
            elif pending.tool:
                self.add_session_rule(pending.session_id, {"kind": "tool", "pattern": pending.tool})

        # Future 结果: (approved: bool, decision: str, reason: str)
        approved = decision in ("approve", "approve_similar")
        if not pending.future.done():
            pending.future.set_result((approved, decision, reason))
        pending.decision = decision
        # 立即从 pending 移除，已通过 future 传递结果
        self._pending.pop(approval_id, None)
        try:
            from app.core.rtstore import delete_pending, fire_and_forget

            fire_and_forget(delete_pending(pending.session_id, approval_id))
        except Exception:
            logger.debug("delete pending redis failed", exc_info=True)
        logger.info("Approval resolved: %s -> %s (%s)", approval_id, decision, reason)
        return True

    def reject_all_for_session(self, session_id: str, reason: str = "disconnect") -> None:
        """WS 断连/停止时批量拒绝"""
        for aid, p in list(self._pending.items()):
            if p.session_id == session_id and not p.future.done():
                p.future.set_result((False, "rejected", reason))
                self._pending.pop(aid, None)
                logger.info("Approval auto-rejected: %s reason=%s", aid, reason)
        try:
            from app.core.rtstore import fire_and_forget, replace_pending

            fire_and_forget(replace_pending(session_id, []))
        except Exception:
            logger.debug("clear pending redis failed", exc_info=True)

    def list_pending(self, session_id: str | None = None) -> list[PendingApproval]:
        if session_id is None:
            return list(self._pending.values())
        return [p for p in self._pending.values() if p.session_id == session_id]

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)


# 全局单例 (单进程硬约束)
gate = ApprovalGate()
