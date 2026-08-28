"""spawn_subagent / finish_subagent — 交互型 (M2)"""

from langchain_core.tools import tool


@tool
def spawn_subagent(behavior_desc: str, goal: str) -> str:
    """派生交互型子 agent 与用户临时对话。参数: behavior_desc(行为描述供快照), goal(对话目标)。侧栏打开，完成后异步回投主 agent。"""
    return f"[占位] spawn_subagent behavior={behavior_desc[:40]} goal={goal[:40]}"


@tool
def finish_subagent(summary: str) -> str:
    """交互型收敛：结束对话并将摘要回投主 agent。参数: summary"""
    return f"[占位] finish_subagent summary={summary[:100]}"
