"""用户门分流：黑名单 / 配置放行 / 会话放行 / 审批

policy(call):
  黑名单 → blocked
  配置放行 → config_allow
  会话放行 → session_allow
  只读工具 → config_allow (可配置需审批)
  其余 → 审批
"""

from app.core.config import settings
from app.permissions.rules import (
    is_blacklisted,
    is_session_shell_allowed,
    is_session_tool_allowed,
    is_shell_prefix_allowed,
    is_tool_allowed,
)
from app.tools.registry import READONLY_TOOLS


def check_policy(tool_name: str, args: dict, session_rules: list[dict]) -> tuple[str, str, bool]:
    """
    返回 (decision, reason, needs_approval)
    decision ∈ {blocked, config_allow, session_allow, need_approval}
    """
    # 提取 shell 命令 (若是 shell 工具)
    command = ""
    if tool_name == "shell":
        command = args.get("command", "") or args.get("cmd", "") or ""

    # 1. 黑名单 (仅对 shell)
    if tool_name == "shell" and command:
        hit, desc = is_blacklisted(command)
        if hit:
            return "blocked", f"命中黑名单: {desc} — {command[:80]}", False

    # 2. 配置放行
    if tool_name == "shell" and command and is_shell_prefix_allowed(command):
        return "config_allow", f"命中配置放行: {command[:80]}", False
    if is_tool_allowed(tool_name):
        return "config_allow", f"工具放行: {tool_name}", False

    # 3. 会话放行
    if tool_name == "shell" and command and is_session_shell_allowed(command, session_rules):
        return "session_allow", f"命中会话放行: {command[:80]}", False
    if is_session_tool_allowed(tool_name, session_rules):
        return "session_allow", f"工具会话放行: {tool_name}", False

    # 4. 只读工具默认放行
    if tool_name in READONLY_TOOLS and not settings.readonly_need_approval:
        return "config_allow", f"只读默认放行: {tool_name}", False

    # 5. 其余需审批
    reason = f"{tool_name}({', '.join(f'{k}={str(v)[:40]}' for k, v in args.items())})" if args else tool_name
    if len(reason) > 120:
        reason = reason[:120] + "..."
    return "need_approval", reason, True
